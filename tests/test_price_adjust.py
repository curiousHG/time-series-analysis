"""Split / unit-split adjustment used by metric computation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.price_adjust import adjust_ohlc_splits, adjust_splits, detect_splits, split_factors


def _series(values: list[float], start: str = "2024-01-01") -> pd.Series:
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)))


def test_etf_one_to_ten_unit_split_is_detected_and_rescaled():
    s = _series([150.0, 151.0, 152.0, 149.0, 151.0, 15.1, 15.2, 15.0, 15.3, 15.1, 15.4, 15.2])
    splits = detect_splits(s)
    assert len(splits) == 1 and splits[0][1] == pytest.approx(0.1)
    adj = adjust_splits(s)
    assert adj.iloc[0] == pytest.approx(15.0)
    assert adj.iloc[-1] == 15.2  # post-split values untouched
    assert (adj.pct_change().dropna().abs() < 0.05).all()


def test_stock_five_for_one_split_with_same_day_price_move():
    s = _series([100.0, 101.0, 99.0, 100.0, 102.0, 21.0, 20.8, 21.2, 20.9, 21.1, 21.3])
    adj = adjust_splits(s)
    assert adj.iloc[0] == pytest.approx(20.0)
    assert detect_splits(s)[0][1] == pytest.approx(0.2)


def test_upward_scale_break_is_handled_too():
    s = _series([14.5, 14.6, 14.7, 14.6, 14.8, 1470.0, 1472.0, 1469.0, 1475.0, 1471.0, 1478.0])
    adj = adjust_splits(s)
    assert adj.iloc[0] == pytest.approx(1450.0)


def test_one_day_blip_that_reverts_is_not_a_split():
    s = _series([33.6, 33.7, 33.5, 33.6, 0.34, 33.65, 33.7, 33.8, 33.6, 33.9])
    assert detect_splits(s) == []
    assert adjust_splits(s).equals(s)


def test_genuine_crash_that_is_not_a_round_factor_is_left_alone():
    s = _series([100.0, 101.0, 99.0, 100.0, 44.0, 43.5, 45.0, 44.2, 43.8, 44.5, 44.1])  # -56%: ratio 0.44
    assert detect_splits(s) == []
    s = _series([100.0, 101.0, 99.0, 100.0, 70.0, 69.5, 71.0, 70.2, 69.8, 70.5, 70.1])  # -30%
    assert detect_splits(s) == []


def test_demerger_on_a_clean_factor_is_treated_as_a_restructuring():
    s = _series([773.6, 770.0, 775.0, 772.0, 271.55, 271.55, 294.65, 290.0, 288.0, 292.0, 295.0])
    assert detect_splits(s)[0][1] == pytest.approx(1 / 3)


def test_two_splits_compound():
    s = _series([1000.0] * 6 + [500.0] * 6 + [100.0] * 6)
    adj = adjust_splits(s)
    assert adj.iloc[0] == pytest.approx(100.0)
    assert adj.iloc[8] == pytest.approx(100.0)
    assert np.allclose(adj.pct_change().dropna(), 0.0)


def test_short_or_empty_series_pass_through():
    assert adjust_splits(pd.Series(dtype=float)).empty
    s = _series([10.0, 1.0, 1.1])
    assert adjust_splits(s).equals(s)


def test_split_factors_are_cumulative_and_one_after_the_last_split():
    s = _series([1000.0] * 6 + [500.0] * 6 + [100.0] * 6)
    factors = split_factors(s)
    assert factors.index.equals(s.index)
    assert factors.iloc[0] == pytest.approx(0.1)
    assert factors.iloc[8] == pytest.approx(0.2)
    assert (factors.iloc[12:] == 1.0).all()
    assert (adjust_splits(s) == s * factors).all()


def test_split_factors_are_all_ones_without_a_split():
    s = _series([10.0, 10.5, 10.2, 10.8, 10.4, 10.6, 10.9])
    assert (split_factors(s) == 1.0).all()
    assert split_factors(pd.Series(dtype=float)).empty


def test_adjust_ohlc_splits_scales_prices_and_divides_volume():
    close = _series([100.0, 101.0, 99.0, 100.0, 102.0, 21.0, 20.8, 21.2, 20.9, 21.1, 21.3])
    df = pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.02,
            "Low": close * 0.98,
            "Close": close,
            "Volume": [1000] * 5 + [5000] * 6,
        }
    )
    out = adjust_ohlc_splits(df)
    assert out.index.equals(df.index)
    assert out["Close"].iloc[0] == pytest.approx(20.0)
    assert out["Open"].iloc[0] == pytest.approx(19.8)
    assert out["High"].iloc[0] == pytest.approx(20.4)
    assert out["Low"].iloc[0] == pytest.approx(19.6)
    assert out["Volume"].iloc[0] == pytest.approx(5000)
    assert out["Close"].iloc[-1] == 21.3
    assert out["Volume"].iloc[-1] == 5000
    assert df["Close"].iloc[0] == 100.0


def test_adjust_ohlc_splits_passes_through_without_a_split():
    close = _series([10.0, 10.5, 10.2, 10.8, 10.4, 10.6, 10.9])
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 10})
    out = adjust_ohlc_splits(df)
    assert out["Close"].equals(df["Close"])
    assert (out["Volume"] == 10).all()
