import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import strategies  # noqa: F401
from strategies.basket import BASKET_STRATEGY_REGISTRY, ENTER, EXIT, SCORE, MarketContext, finalize_signals
from strategies.ml.direction_classifier import MLDirectionClassifier
from strategies.ml.features import DO_PREDICT_COL, PRED_COL, TARGET_PREFIX, startup_bars
from strategies.ml.labels import label_column
from strategies.ml.ml_strategy import MLStrategy
from strategies.ml.return_ranker import MLReturnRanker

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_FILES = ("strategies/ml/direction_classifier.py", "strategies/ml/return_ranker.py")


def synthetic_ohlcv(n: int = 400, seed: int = 7, start: str = "2020-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    log_ret = rng.normal(0.0004, 0.015, n)
    close = 100 * np.exp(np.cumsum(log_ret))
    open_ = close * np.exp(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.006, n)))
    volume = rng.lognormal(12, 0.4, n).round()
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


def _frames(n: int = 520) -> dict[str, pd.DataFrame]:
    return {sym: synthetic_ohlcv(n, seed=seed) for sym, seed in (("AAA", 1), ("BBB", 2), ("CCC", 3))}


def _run(strategy: MLStrategy, frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    ctx = MarketContext(calendar=next(iter(frames.values())).index, universe=tuple(frames))
    frames = {sym: strategy.populate_indicators(df.copy(), sym, ctx) for sym, df in frames.items()}
    frames = strategy.prepare_basket(frames, progress_cb=lambda **_: None)
    out = {}
    for sym, df in frames.items():
        df = strategy.populate_entry(df, sym, ctx)
        df = strategy.populate_exit(df, sym, ctx)
        df = strategy.populate_score(df, sym, ctx)
        out[sym] = finalize_signals(df, mode=strategy.mode)
    return out


def _fast(cls, **params):
    return cls(train_days=126, test_days=21, horizon=3, **params)


def test_both_strategies_are_registered_with_expected_modes():
    assert BASKET_STRATEGY_REGISTRY["ML Direction Classifier"] is MLDirectionClassifier
    assert BASKET_STRATEGY_REGISTRY["ML Return Ranker"] is MLReturnRanker
    assert MLDirectionClassifier.mode == "signal"
    assert MLReturnRanker.mode == "rank"


def test_parameter_space_marks_walk_forward_knobs_non_optimised():
    space = MLDirectionClassifier.parameter_space()
    for key in ("feature_set", "horizon", "train_days", "test_days", "pooled", "expanding", "pca_components"):
        assert space[key].optimize is False, key
    assert space["importance_method"].optimize is False
    for key in ("model", "n_estimators", "learning_rate", "num_leaves", "max_depth", "min_child_samples"):
        assert space[key].optimize is True, key
    assert space["entry_threshold"].optimize and space["exit_threshold"].optimize
    assert set(space["model"].choices) <= set(MLDirectionClassifier.parameter_space()["model"].choices)


def test_startup_candle_count_accounts_for_features_and_training():
    strat = _fast(MLDirectionClassifier)
    assert strat.startup_candle_count == startup_bars(strat.feature_names()) + 126 + 3 + 1
    full = _fast(MLDirectionClassifier, feature_set="full")
    assert full.startup_candle_count >= strat.startup_candle_count


def test_populate_indicators_adds_features_and_label():
    strat = _fast(MLDirectionClassifier)
    ctx = MarketContext(calendar=pd.DatetimeIndex([]), universe=("AAA",))
    df = strat.populate_indicators(synthetic_ohlcv(300), "AAA", ctx)
    assert set(strat.feature_names()) <= set(df.columns)
    label = label_column(strat.label_name)
    assert label in df.columns
    assert df[label].iloc[-3:].isna().all()
    assert set(df[label].dropna().unique()) <= {0.0, 1.0}


def test_direction_classifier_signal_columns_respect_do_predict():
    strat = _fast(MLDirectionClassifier, model="logistic")
    out = _run(strat, _frames())
    for df in out.values():
        assert df[ENTER].dtype == bool and df[EXIT].dtype == bool
        assert (df.loc[df[ENTER], DO_PREDICT_COL] == 1).all()
        assert (df.loc[df[EXIT], DO_PREDICT_COL] == 1).all()
        assert not (df[ENTER] & df[EXIT]).any()
        assert df[SCORE].isna().equals(df[DO_PREDICT_COL] == 0)
        first_complete = df.index.get_loc(df[strat.feature_names()].dropna().index[0])
        first_pred = df.index.get_loc(df[DO_PREDICT_COL].eq(1).idxmax())
        assert first_pred == first_complete + strat.train_days + strat.horizon
        assert first_pred <= strat.startup_candle_count - 1
        assert (df[DO_PREDICT_COL].iloc[:first_pred] == 0).all()
        assert df[DO_PREDICT_COL].iloc[strat.startup_candle_count :].eq(1).mean() > 0.9
    diag = strat.diagnostics()
    assert diag is not None and diag.summary["n_windows"] > 0
    assert set(diag.coverage.index) == {"AAA", "BBB", "CCC"}


def test_return_ranker_score_is_nan_exactly_where_do_predict_is_zero():
    strat = _fast(MLReturnRanker, model="ridge")
    out = _run(strat, _frames())
    for df in out.values():
        assert df[SCORE].isna().equals(df[DO_PREDICT_COL] == 0)
        assert df[SCORE].dropna().equals(df.loc[df[DO_PREDICT_COL] == 1, PRED_COL])
        assert df[ENTER].dtype == bool and df[EXIT].dtype == bool
        assert (df.loc[df[ENTER], DO_PREDICT_COL] == 1).all()
    assert strat.diagnostics().summary["kind"] == "regressor"


def test_return_ranker_zscore_entries_only_after_enough_predictions():
    strat = _fast(MLReturnRanker, model="ridge", entry_z=0.5, exit_z=-0.5)
    out = _run(strat, _frames())
    for df in out.values():
        first_pred = df[DO_PREDICT_COL].eq(1).idxmax()
        pos = df.index.get_loc(first_pred)
        assert not df[ENTER].iloc[: pos + strat.zscore_window - 1].any()
        assert df[ENTER].any()
        assert df[EXIT].any()


def test_model_choice_flows_into_predictor_params():
    strat = _fast(MLDirectionClassifier, model="hist_gb_clf", n_estimators=50, num_leaves=7)
    params = strat.model_params()
    assert params["max_iter"] == 50 and params["max_leaf_nodes"] == 7
    ridge = _fast(MLReturnRanker, model="ridge")
    assert ridge.model_params() == {}
    with pytest.raises(TypeError):
        MLReturnRanker(nope=1)


def test_diagnostics_is_none_before_prepare_basket():
    assert _fast(MLReturnRanker).diagnostics() is None


@pytest.mark.parametrize("rel", STRATEGY_FILES)
def test_entry_and_exit_code_never_read_a_label_column(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(TARGET_PREFIX):
            assert node.value == PRED_COL, f"{rel} references label column {node.value!r}"
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "label_column(" not in text
    assert "build_label(" not in text


def test_pred_column_is_the_only_target_prefixed_column_written_by_prepare_basket():
    strat = _fast(MLReturnRanker, model="ridge")
    out = _run(strat, _frames())
    for df in out.values():
        targets = [c for c in df.columns if c.startswith(TARGET_PREFIX)]
        assert sorted(targets) == sorted([PRED_COL, label_column(strat.label_name)])
        assert np.isfinite(df.loc[df[DO_PREDICT_COL] == 1, PRED_COL]).all()
