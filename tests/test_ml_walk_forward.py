import numpy as np
import pandas as pd
import pytest

from strategies.ml.diagnostics import MLDiagnostics, build_diagnostics
from strategies.ml.predictors import make_predictor
from strategies.ml.walk_forward import (
    WalkForwardConfig,
    WalkForwardResult,
    Window,
    feature_calendar,
    make_windows,
    run_walk_forward,
    trading_calendar,
)

FEATURES = ["%-a", "%-b", "%-c"]
LABEL = "&-y"


def _planted(symbols=("AAA", "BBB"), n=600, seed=0, warmup=30) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    frames = {}
    for sym in symbols:
        X = rng.normal(size=(n, 3))
        y = 0.8 * X[:, 0] - 0.5 * X[:, 1] + 0.3 * rng.normal(size=n)
        df = pd.DataFrame(X, index=idx, columns=FEATURES)
        df["Close"] = 100.0
        df[LABEL] = y
        df.iloc[:warmup, df.columns.get_loc("%-a")] = np.nan
        df.iloc[-5:, df.columns.get_loc(LABEL)] = np.nan
        frames[sym] = df
    return frames


def _cfg(**kw) -> WalkForwardConfig:
    base = {"train_days": 200, "test_days": 50, "horizon": 5, "min_train_rows": 50}
    base.update(kw)
    return WalkForwardConfig(**base)


def _ridge():
    return make_predictor("ridge", {"alpha": 1.0})


def test_window_arithmetic_sliding_and_expanding():
    cal = pd.bdate_range("2020-01-01", periods=400)
    cfg = _cfg(train_days=100, test_days=30, horizon=5)
    windows = make_windows(cal, cfg)
    assert windows
    first = windows[0]
    assert isinstance(first, Window)
    assert first.idx == 0
    assert first.train_start == cal[0]
    assert first.train_end == cal[99]
    assert first.test_start == cal[105]
    assert first.test_end == cal[134]
    for w in windows:
        pos = cal.get_loc
        assert pos(w.train_end) == pos(w.test_start) - cfg.horizon - 1
        assert pos(w.train_end) - pos(w.train_start) + 1 == cfg.train_days
        assert pos(w.test_end) - pos(w.test_start) + 1 <= cfg.test_days
    assert windows[1].test_start == cal[135]
    assert windows[-1].test_end == cal[-1]
    covered = sum(cal.get_loc(w.test_end) - cal.get_loc(w.test_start) + 1 for w in windows)
    assert covered == 400 - 105

    expanding = make_windows(cal, _cfg(train_days=100, test_days=30, horizon=5, expanding=True))
    assert all(w.train_start == cal[0] for w in expanding)
    assert [w.test_start for w in expanding] == [w.test_start for w in windows]
    assert make_windows(cal[:50], cfg) == []


def test_trading_calendar_is_the_union_of_indices():
    a = pd.bdate_range("2020-01-01", periods=10)
    b = pd.bdate_range("2020-01-08", periods=10)
    frames = {"A": pd.DataFrame(index=a), "B": pd.DataFrame(index=b)}
    cal = trading_calendar(frames)
    assert cal.equals(a.union(b))


def test_feature_calendar_skips_the_warm_up():
    frames = _planted(warmup=30)
    cal = feature_calendar(frames, FEATURES)
    assert cal[0] == frames["AAA"].index[30]
    assert len(cal) == 600 - 30


def test_planted_signal_is_recovered_by_ridge_per_window():
    frames = _planted()
    seen = []
    result = run_walk_forward(
        frames,
        FEATURES,
        LABEL,
        "regressor",
        _cfg(),
        _ridge,
        progress_cb=lambda **fields: seen.append(fields),
    )
    assert isinstance(result, WalkForwardResult)
    assert result.feature_names == FEATURES
    assert len(result.windows) >= 5
    assert all(w.metrics["ic"] > 0.3 for w in result.windows)
    assert all(w.n_train > 0 and w.n_test > 0 for w in result.windows)
    assert all(w.importance is not None and len(w.importance) == 3 for w in result.windows)
    assert seen and seen[-1]["phase"] == "walk-forward" and seen[-1]["done"] == seen[-1]["total"]


def test_do_predict_zero_before_first_window_and_on_nan_rows():
    frames = _planted()
    frames["AAA"].iloc[300, frames["AAA"].columns.get_loc("%-b")] = np.nan
    cfg = _cfg()
    result = run_walk_forward(frames, FEATURES, LABEL, "regressor", cfg, _ridge)
    first_pos = 30 + cfg.train_days + cfg.horizon
    for sym, flags in result.do_predict.items():
        assert flags.index.equals(frames[sym].index)
        assert (flags.iloc[:first_pos] == 0).all()
        assert (flags.iloc[first_pos:].drop(flags.index[300]) == 1).all()
        assert result.predictions[sym].isna().equals(flags == 0)
    assert result.do_predict["AAA"].iloc[300] == 0
    assert result.do_predict["BBB"].iloc[300] == 1


def test_walk_forward_is_truncation_invariant():
    frames = _planted()
    cut = {sym: df.iloc[:-60] for sym, df in frames.items()}
    full = run_walk_forward(frames, FEATURES, LABEL, "regressor", _cfg(), _ridge)
    part = run_walk_forward(cut, FEATURES, LABEL, "regressor", _cfg(), _ridge)
    for sym in frames:
        a = full.predictions[sym].iloc[:-60]
        b = part.predictions[sym]
        np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=1e-9, atol=1e-12, equal_nan=True)


def test_per_symbol_models_and_pca_and_permutation_importance():
    frames = _planted()
    cfg = _cfg(pooled=False, pca_components=3, importance="permutation", scaler="robust")
    result = run_walk_forward(frames, FEATURES, LABEL, "regressor", cfg, _ridge)
    symbols = {w.symbol for w in result.windows}
    assert symbols == {"AAA", "BBB"}
    assert all(len(w.importance) == 3 for w in result.windows)
    assert all(w.metrics["ic"] > 0.3 for w in result.windows)

    none = run_walk_forward(frames, FEATURES, LABEL, "regressor", _cfg(importance="none"), _ridge)
    assert all(w.importance is None for w in none.windows)


def test_classifier_metrics_and_diagnostics():
    frames = _planted()
    for df in frames.values():
        df[LABEL] = (df[LABEL] > 0).astype(float).where(df[LABEL].notna())
    cfg = _cfg()
    result = run_walk_forward(frames, FEATURES, LABEL, "classifier", cfg, lambda: make_predictor("logistic"))
    assert all({"auc", "hit_rate", "log_loss"} <= set(w.metrics) for w in result.windows)
    assert all(w.metrics["auc"] > 0.7 for w in result.windows)
    for series in result.predictions.values():
        valid = series.dropna()
        assert ((valid >= 0) & (valid <= 1)).all()

    diag = build_diagnostics(result, frames, LABEL)
    assert isinstance(diag, MLDiagnostics)
    assert diag.summary["n_windows"] == len(result.windows)
    assert diag.summary["auc_mean"] > 0.7
    assert len(diag.windows) == len(result.windows)
    assert {"idx", "symbol", "n_train", "n_test", "fit_seconds", "auc"} <= set(diag.windows.columns)
    assert list(diag.importance.index) and set(diag.importance.index) == set(FEATURES)
    assert diag.importance["importance"].iloc[0] >= diag.importance["importance"].iloc[-1]
    assert set(diag.predictions.columns) >= {"date", "symbol", "pred", "do_predict", "label"}
    assert set(diag.coverage.index) == {"AAA", "BBB"}
    assert 0 < diag.coverage["AAA"] < 1


def test_min_train_rows_skips_thin_windows():
    frames = _planted(n=260)
    cfg = _cfg(train_days=200, test_days=20, horizon=5, min_train_rows=10_000)
    result = run_walk_forward(frames, FEATURES, LABEL, "regressor", cfg, _ridge)
    assert result.windows == []
    assert all((flags == 0).all() for flags in result.do_predict.values())
    diag = build_diagnostics(result, frames, LABEL)
    assert diag.summary["n_windows"] == 0
    assert diag.coverage["AAA"] == pytest.approx(0.0)
