import numpy as np
import pandas as pd
import pytest

from strategies.ml.labels import LABEL_REGISTRY, TARGET_PREFIX, build_label, label_column


def _frame() -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=8)
    close = [100.0, 102.0, 101.0, 105.0, 103.0, 108.0, 110.0, 109.0]
    open_ = [99.0, 101.0, 102.0, 104.0, 104.0, 107.0, 109.0, 110.0]
    high = [c + 3 for c in close]
    low = [c - 3 for c in close]
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1000}, index=idx)


def test_registry_and_column_names():
    assert set(LABEL_REGISTRY) == {"fwd_return", "direction", "atr_fwd_gain", "triple_barrier"}
    assert LABEL_REGISTRY["fwd_return"].kind == "regressor"
    assert LABEL_REGISTRY["direction"].kind == "classifier"
    assert label_column("direction") == f"{TARGET_PREFIX}direction"
    with pytest.raises(KeyError):
        build_label(_frame(), "nope", 1)


def test_fwd_return_is_log_close_to_close():
    df = _frame()
    y = build_label(df, "fwd_return", 2)
    assert y.name == label_column("fwd_return")
    assert y.iloc[0] == pytest.approx(np.log(101 / 100))
    assert y.iloc[5] == pytest.approx(np.log(109 / 108))
    assert y.iloc[-2:].isna().all()


def test_fwd_return_realizable_uses_next_open():
    df = _frame()
    y = build_label(df, "fwd_return", 2, realizable=True)
    assert y.iloc[0] == pytest.approx(np.log(105 / 101))
    assert y.iloc[-3:].isna().all()


def test_direction_thresholds_the_forward_return():
    df = _frame()
    y = build_label(df, "direction", 1)
    assert y.iloc[:7].tolist() == [1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
    assert np.isnan(y.iloc[7])
    strict = build_label(df, "direction", 1, threshold=0.03)
    assert strict.iloc[:7].tolist() == [0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0]


def test_atr_fwd_gain_normalises_by_natr_and_clips():
    df = _frame()
    natr = pd.Series(np.full(len(df), 2.0), index=df.index)
    y = build_label(df, "atr_fwd_gain", 1, natr=natr)
    assert y.iloc[0] == pytest.approx(np.log(102 / 100) / 0.02)
    tiny = pd.Series(np.full(len(df), 0.001), index=df.index)
    clipped = build_label(df, "atr_fwd_gain", 1, natr=tiny)
    assert clipped.iloc[0] == pytest.approx(5.0)
    assert clipped.iloc[2] == pytest.approx(5.0)
    assert clipped.iloc[1] == pytest.approx(-5.0)
    assert np.isnan(y.iloc[-1])


def test_atr_fwd_gain_computes_natr_when_absent():
    df = pd.concat([_frame()] * 4)
    df.index = pd.bdate_range("2024-01-01", periods=len(df))
    y = build_label(df, "atr_fwd_gain", 1)
    assert y.iloc[:14].isna().all()
    assert y.iloc[14:-1].notna().all()


def test_triple_barrier_first_touch_wins():
    idx = pd.bdate_range("2024-01-01", periods=6)
    close = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    high = [101.0, 101.0, 106.0, 101.0, 101.0, 101.0]
    low = [99.0, 99.0, 99.0, 94.0, 99.0, 99.0]
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": 1}, index=idx)
    natr = pd.Series(np.full(6, 2.5), index=idx)
    y = build_label(df, "triple_barrier", 3, natr=natr, k=2.0)
    assert y.iloc[0] == 1.0
    assert y.iloc[1] == 1.0
    assert y.iloc[2] == -1.0
    assert np.isnan(y.iloc[3])
    assert np.isnan(y.iloc[-1])


def test_triple_barrier_vertical_and_both_same_bar():
    idx = pd.bdate_range("2024-01-01", periods=6)
    close = [100.0] * 6
    high = [101.0, 101.0, 101.0, 101.0, 110.0, 101.0]
    low = [99.0, 99.0, 99.0, 99.0, 90.0, 99.0]
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": 1}, index=idx)
    natr = pd.Series(np.full(6, 2.5), index=idx)
    y = build_label(df, "triple_barrier", 2, natr=natr, k=2.0)
    assert y.iloc[0] == 0.0
    assert y.iloc[1] == 0.0
    assert y.iloc[2] == -1.0
    assert y.iloc[3] == -1.0
    assert y.iloc[4:].isna().all()
