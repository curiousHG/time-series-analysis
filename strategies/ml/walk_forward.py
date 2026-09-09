"""Purged walk-forward training and prediction over a basket of frames.

Windows are laid out on the union calendar of feature-complete rows: each test block of `test_days` bars is
predicted by a model trained on the `train_days` bars ending `horizon + 1` bars before it, so no
training label overlaps the test period. Scaler and PCA are fit on the training window only.
Predictions are stored on the bar they were made on; the engine applies the one-bar shift.
Importance is native when the model exposes it (and no PCA is in the way), otherwise permutation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Callable

    from strategies.ml.predictors import Predictor

log = logging.getLogger(__name__)

ScalerKind = Literal["standard", "robust", "none"]
ImportanceKind = Literal["native", "permutation", "none"]
PERMUTATION_REPEATS = 3


@dataclass(frozen=True)
class WalkForwardConfig:
    train_days: int
    test_days: int
    horizon: int
    expanding: bool = False
    pooled: bool = True
    scaler: ScalerKind = "standard"
    pca_components: int = 0
    min_train_rows: int = 250
    importance: ImportanceKind = "native"


@dataclass(frozen=True)
class Window:
    idx: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass
class WindowReport:
    window: Window
    symbol: str
    n_train: int
    n_test: int
    fit_seconds: float
    metrics: dict[str, float]
    importance: np.ndarray | None


@dataclass
class WalkForwardResult:
    predictions: dict[str, pd.Series]
    do_predict: dict[str, pd.Series]
    windows: list[WindowReport]
    feature_names: list[str] = field(default_factory=list)


def trading_calendar(frames: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    cal = pd.DatetimeIndex([])
    for df in frames.values():
        cal = cal.union(df.index)
    return cal.sort_values()


def feature_calendar(frames: dict[str, pd.DataFrame], feature_cols: list[str]) -> pd.DatetimeIndex:
    """Union of the dates on which at least one symbol has a complete feature row.

    Laying the windows on this calendar (rather than the raw one) means the first window trains on
    `train_days` usable bars and the first prediction lands at `startup_bars + train_days + horizon`.
    """
    return trading_calendar({sym: df[_valid_rows(df, feature_cols, None)] for sym, df in frames.items()})


def make_windows(cal: pd.DatetimeIndex, cfg: WalkForwardConfig) -> list[Window]:
    n = len(cal)
    windows: list[Window] = []
    test_start = cfg.train_days + cfg.horizon
    while test_start < n:
        train_end = test_start - cfg.horizon - 1
        train_start = 0 if cfg.expanding else train_end - cfg.train_days + 1
        test_end = min(test_start + cfg.test_days - 1, n - 1)
        windows.append(Window(len(windows), cal[train_start], cal[train_end], cal[test_start], cal[test_end]))
        test_start += cfg.test_days
    return windows


class _Transform:
    """Scaler then optional PCA, both fit on the training block only."""

    def __init__(self, scaler: ScalerKind, pca_components: int) -> None:
        self.scaler = self._make_scaler(scaler)
        self.pca = None
        self.pca_components = pca_components

    @staticmethod
    def _make_scaler(kind: ScalerKind):
        if kind == "none":
            return None
        from sklearn.preprocessing import RobustScaler, StandardScaler  # noqa: PLC0415

        return RobustScaler() if kind == "robust" else StandardScaler()

    def fit(self, X: np.ndarray) -> _Transform:
        if self.scaler is not None:
            X = self.scaler.fit_transform(X)
        if self.pca_components > 0:
            from sklearn.decomposition import PCA  # noqa: PLC0415

            self.pca = PCA(n_components=min(self.pca_components, X.shape[1]), random_state=0).fit(X)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.scaler is not None:
            X = self.scaler.transform(X)
        if self.pca is not None:
            X = self.pca.transform(X)
        return X


class _Pipeline:
    """Fitted transform + model; `fit` is a no-op so sklearn's permutation_importance accepts it."""

    def __init__(self, transform: _Transform, model: Predictor) -> None:
        self.transform = transform
        self.model = model

    def fit(self, X: np.ndarray, y: np.ndarray | None = None) -> _Pipeline:
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(self.transform.transform(X))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


def regressor_metrics(pred: np.ndarray, y: np.ndarray) -> dict[str, float]:
    if len(y) == 0:
        return {"ic": float("nan"), "hit_rate": float("nan"), "rmse": float("nan")}
    return {
        "ic": _spearman(pred, y),
        "hit_rate": float(np.mean(np.sign(pred) == np.sign(y))),
        "rmse": float(np.sqrt(np.mean((pred - y) ** 2))),
    }


def classifier_metrics(pred: np.ndarray, y: np.ndarray) -> dict[str, float]:
    if len(y) == 0:
        return {"auc": float("nan"), "hit_rate": float("nan"), "log_loss": float("nan")}
    from sklearn.metrics import log_loss, roc_auc_score  # noqa: PLC0415

    positive = (y == 1).astype(int)
    auc = float(roc_auc_score(positive, pred)) if positive.min() != positive.max() else float("nan")
    clipped = np.clip(pred, 1e-6, 1 - 1e-6)
    return {
        "auc": auc,
        "hit_rate": float(np.mean((pred > 0.5) == (positive == 1))),
        "log_loss": float(log_loss(positive, clipped, labels=[0, 1])),
    }


def _score_fn(kind: str) -> Callable[[Any, np.ndarray, np.ndarray], float]:
    if kind == "classifier":
        return lambda est, X, y: -classifier_metrics(est.predict(X), y)["log_loss"]
    return lambda est, X, y: regressor_metrics(est.predict(X), y)["ic"]


def _importance(
    cfg: WalkForwardConfig, pipeline: _Pipeline, X_test: np.ndarray, y_test: np.ndarray, kind: str, n_features: int
) -> np.ndarray | None:
    if cfg.importance == "none":
        return None
    if cfg.importance == "native" and pipeline.transform.pca is None:
        native = pipeline.model.feature_importance()
        if native is not None and len(native) == n_features:
            return native
    if len(y_test) < 3:
        return None
    from sklearn.inspection import permutation_importance  # noqa: PLC0415

    result = permutation_importance(
        pipeline, X_test, y_test, scoring=_score_fn(kind), n_repeats=PERMUTATION_REPEATS, random_state=0
    )
    return np.asarray(result.importances_mean, dtype=float)


def _valid_rows(df: pd.DataFrame, feature_cols: list[str], label_col: str | None) -> pd.Series:
    cols = feature_cols if label_col is None else [*feature_cols, label_col]
    return df[cols].notna().all(axis=1)


def _slice(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df.loc[(df.index >= start) & (df.index <= end)]


def _train_block(
    frames: dict[str, pd.DataFrame], symbols: list[str], window: Window, feature_cols: list[str], label_col: str
) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for sym in symbols:
        block = _slice(frames[sym], window.train_start, window.train_end)
        block = block[_valid_rows(block, feature_cols, label_col)]
        if len(block):
            xs.append(block[feature_cols].to_numpy(dtype=float))
            ys.append(block[label_col].to_numpy(dtype=float))
    if not xs:
        return np.empty((0, len(feature_cols))), np.empty(0)
    return np.vstack(xs), np.concatenate(ys)


def _fit_window(
    frames: dict[str, pd.DataFrame],
    symbols: list[str],
    window: Window,
    feature_cols: list[str],
    label_col: str,
    kind: str,
    cfg: WalkForwardConfig,
    predictor_factory: Callable[[], Predictor],
    predictions: dict[str, pd.Series],
    do_predict: dict[str, pd.Series],
    report_symbol: str,
) -> WindowReport | None:
    X_train, y_train = _train_block(frames, symbols, window, feature_cols, label_col)
    if len(y_train) < cfg.min_train_rows:
        log.debug(
            "window %d/%s skipped: %d training rows < %d", window.idx, report_symbol, len(y_train), cfg.min_train_rows
        )
        return None
    if kind == "classifier" and len(np.unique(y_train)) < 2:
        log.debug("window %d/%s skipped: single class in training labels", window.idx, report_symbol)
        return None
    started = time.perf_counter()
    transform = _Transform(cfg.scaler, cfg.pca_components).fit(X_train)
    model = predictor_factory().fit(transform.transform(X_train), y_train)
    fit_seconds = time.perf_counter() - started
    pipeline = _Pipeline(transform, model)

    test_x, test_y = [], []
    n_test = 0
    for sym in symbols:
        block = _slice(frames[sym], window.test_start, window.test_end)
        usable = block[_valid_rows(block, feature_cols, None)]
        if usable.empty:
            continue
        pred = pipeline.predict(usable[feature_cols].to_numpy(dtype=float))
        predictions[sym].loc[usable.index] = pred
        do_predict[sym].loc[usable.index] = 1
        n_test += len(usable)
        known = usable[label_col].notna().to_numpy()
        if known.any():
            test_x.append(usable[feature_cols].to_numpy(dtype=float)[known])
            test_y.append(usable[label_col].to_numpy(dtype=float)[known])
    X_test = np.vstack(test_x) if test_x else np.empty((0, len(feature_cols)))
    y_test = np.concatenate(test_y) if test_y else np.empty(0)
    pred_test = pipeline.predict(X_test) if len(y_test) else np.empty(0)
    metrics = classifier_metrics(pred_test, y_test) if kind == "classifier" else regressor_metrics(pred_test, y_test)
    importance = _importance(cfg, pipeline, X_test, y_test, kind, len(feature_cols))
    return WindowReport(window, report_symbol, len(y_train), n_test, fit_seconds, metrics, importance)


def run_walk_forward(
    frames: dict[str, pd.DataFrame],
    feature_cols: list[str],
    label_col: str,
    kind: str,
    cfg: WalkForwardConfig,
    predictor_factory: Callable[[], Predictor],
    progress_cb: Callable[..., None] | None = None,
) -> WalkForwardResult:
    feature_cols = list(feature_cols)
    cal = feature_calendar(frames, feature_cols)
    windows = make_windows(cal, cfg)
    predictions = {sym: pd.Series(np.nan, index=df.index, dtype=float, name="pred") for sym, df in frames.items()}
    do_predict = {sym: pd.Series(0, index=df.index, dtype=int, name="do_predict") for sym, df in frames.items()}
    reports: list[WindowReport] = []
    symbols = list(frames)
    units: list[tuple[Window, list[str], str]] = [
        (window, symbols if cfg.pooled else [sym], "pooled" if cfg.pooled else sym)
        for window in windows
        for sym in (["pooled"] if cfg.pooled else symbols)
    ]
    total = len(units)
    for done, (window, members, label) in enumerate(units, start=1):
        report = _fit_window(
            frames,
            members,
            window,
            feature_cols,
            label_col,
            kind,
            cfg,
            predictor_factory,
            predictions,
            do_predict,
            label,
        )
        if report is not None:
            reports.append(report)
        if progress_cb is not None:
            progress_cb(
                phase="walk-forward",
                done=done,
                total=total,
                message=f"window {window.idx + 1}/{len(windows)} ({label}) {window.test_start:%Y-%m-%d}",
            )
    log.info("walk-forward: %d windows, %d fits, %d symbols", len(windows), len(reports), len(symbols))
    return WalkForwardResult(predictions, do_predict, reports, feature_cols)
