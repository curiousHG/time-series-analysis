"""Split / unit-split adjustment used by metric computation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.price_adjust import adjust_splits, detect_splits


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
