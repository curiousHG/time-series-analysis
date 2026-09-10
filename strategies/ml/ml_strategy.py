"""Base class for machine-learned basket strategies.

`populate_indicators` builds the feature set and the training label per symbol; `prepare_basket`
runs the purged walk-forward once over the basket and writes `&-pred` and `do_predict` back onto
every frame. Subclasses turn the prediction into entries, exits or a rank score and must read
`&-pred` only, never the label column.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

import pandas as pd

from strategies.basket import BasketStrategy
from strategies.ml.diagnostics import MLDiagnostics, build_diagnostics
from strategies.ml.features import DO_PREDICT_COL, PRED_COL, build_features, feature_names, startup_bars
from strategies.ml.labels import LABEL_REGISTRY, build_label, label_column
from strategies.ml.predictors import make_predictor
from strategies.ml.walk_forward import WalkForwardConfig, run_walk_forward
from strategies.parameters import BoolParameter, CategoricalParameter, IntParameter

if TYPE_CHECKING:
    from collections.abc import Callable

    from strategies.basket import MarketContext

log = logging.getLogger(__name__)

MIN_TRAIN_ROWS_FLOOR = 50


class MLStrategy(BasketStrategy):
    name: ClassVar[str] = "ML Base"
    model_name: ClassVar[str] = "ridge"
    label_name: ClassVar[str] = "fwd_return"
    seed: int = 42

    feature_set = CategoricalParameter("core", ("core", "full"), optimize=False, help="Feature registry set")
    horizon = IntParameter(5, 1, 63, optimize=False, help="Label horizon in bars")
    train_days = IntParameter(504, 126, 1260, step=21, optimize=False, help="Training window in bars")
    test_days = IntParameter(63, 5, 252, optimize=False, help="Bars predicted per refit")
    pooled = BoolParameter(True, optimize=False, help="One model across the basket")
    expanding = BoolParameter(False, optimize=False, help="Grow the training window instead of sliding")
    pca_components = IntParameter(0, 0, 30, optimize=False, help="PCA components (0 = off)")
    importance_method = CategoricalParameter(
        "native", ("native", "permutation", "none"), optimize=False, help="Feature importance method"
    )

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self._diagnostics: MLDiagnostics | None = None

    @property
    def startup_candle_count(self) -> int:
        return startup_bars(self.feature_names()) + self.train_days + self.horizon + 1

    @property
    def label_col(self) -> str:
        return label_column(self.label_name)

    @property
    def kind(self) -> str:
        return LABEL_REGISTRY[self.label_name].kind

    def feature_names(self) -> list[str]:
        return feature_names(self.feature_set)

    def predictor_name(self) -> str:
        return self.model_name

    def model_params(self) -> dict[str, Any]:
        return {}

    def label_kwargs(self) -> dict[str, Any]:
        return {}

    def wf_config(self) -> WalkForwardConfig:
        return WalkForwardConfig(
            train_days=self.train_days,
            test_days=self.test_days,
            horizon=self.horizon,
            expanding=self.expanding,
            pooled=self.pooled,
            pca_components=self.pca_components,
            min_train_rows=max(MIN_TRAIN_ROWS_FLOOR, self.train_days // 2),
            importance=self.importance_method,
        )

    def make_model(self):
        return make_predictor(self.predictor_name(), self.model_params(), seed=self.seed)

    def populate_indicators(self, df: pd.DataFrame, symbol: str, ctx: MarketContext) -> pd.DataFrame:
        features = build_features(df, self.feature_names())
        df = df.drop(columns=[c for c in features.columns if c in df.columns]).join(features)
        df[self.label_col] = build_label(df, self.label_name, self.horizon, **self.label_kwargs())
        return df

    def prepare_basket(
        self, frames: dict[str, pd.DataFrame], progress_cb: Callable[..., None] | None = None
    ) -> dict[str, pd.DataFrame]:
        cfg = self.wf_config()
        log.info(
            "%s: %s on %d symbols, %d features, horizon %d, train %d / test %d",
            self.name,
            self.predictor_name(),
            len(frames),
            len(self.feature_names()),
            cfg.horizon,
            cfg.train_days,
            cfg.test_days,
        )
        result = run_walk_forward(
            frames, self.feature_names(), self.label_col, self.kind, cfg, self.make_model, progress_cb=progress_cb
        )
        for sym, df in frames.items():
            df[PRED_COL] = result.predictions[sym]
            df[DO_PREDICT_COL] = result.do_predict[sym].astype(int)
        self._diagnostics = build_diagnostics(result, frames, self.label_col, self.kind)
        return frames

    def diagnostics(self) -> MLDiagnostics | None:
        return self._diagnostics


def prediction_mask(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(prediction, do_predict == 1) with safe defaults when `prepare_basket` has not run."""
    if PRED_COL not in df.columns or DO_PREDICT_COL not in df.columns:
        empty = pd.Series(float("nan"), index=df.index)
        return empty, pd.Series(False, index=df.index)
    return df[PRED_COL].astype(float), df[DO_PREDICT_COL].fillna(0).astype(int) == 1
