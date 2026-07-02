"""Unit tests for rolling_alpha and rolling_beat_rate (services.mf_metrics)."""

import numpy as np
import pandas as pd
import pytest

from services.mf_metrics import rolling_alpha, rolling_beat_rate


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2022-01-03", periods=n)


def test_rolling_alpha_recovers_constructed_drift():
    """fund = 1.0 x bench + constant daily drift → alpha ≈ drift * 252, beta absorbed."""
    rng = np.random.default_rng(42)
    n, window = 500, 126
    bench = pd.Series(rng.normal(0.0004, 0.01, n), index=_dates(n))
    drift = 0.0002  # 5.04% annualised
    fund = bench + drift
    alpha = rolling_alpha(fund, bench, window=window)
    assert len(alpha) == n - window + 1
    assert alpha.mean() == pytest.approx(drift * 252, rel=1e-6)
    assert alpha.std() == pytest.approx(0.0, abs=1e-9)  # exact relationship → constant alpha


def test_rolling_alpha_short_overlap_empty():
    short = pd.Series([0.01] * 50, index=_dates(50))
    assert rolling_alpha(short, short, window=126).empty


def test_rolling_beat_rate_exact_fraction():
    """Fund beats the benchmark in exactly the windows ending in the second half."""
    n, w = 160, 20
    idx = _dates(n)
    bench_prices = pd.Series(100.0 * (1.001 ** np.arange(n)), index=idx)
    # Fund flat in the first half (loses every window), then rises faster (wins every window
    # fully inside the second half).
    fund = np.concatenate([np.full(80, 100.0), 100.0 * (1.005 ** np.arange(1, 81))])
    nav = pd.Series(fund, index=idx)

    rate = rolling_beat_rate(nav, bench_prices, window_days=w, min_windows=10)
    assert rate is not None
    # Windows fully inside the flat half lose; fully inside the rising half win (0.5%>0.1%/day).
    assert 0.3 < rate < 0.7


def test_rolling_beat_rate_insufficient_windows():
    nav = pd.Series([100.0, 101.0], index=_dates(2))
    assert rolling_beat_rate(nav, nav, window_days=20) is None


def test_rolling_beat_rate_always_beats():
    n, w = 120, 20
    idx = _dates(n)
    bench = pd.Series(100.0 * (1.0001 ** np.arange(n)), index=idx)
    nav = pd.Series(100.0 * (1.001 ** np.arange(n)), index=idx)
    assert rolling_beat_rate(nav, bench, window_days=w) == pytest.approx(1.0)
