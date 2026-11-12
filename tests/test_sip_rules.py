"""Monthly-SIP rule simulator on synthetic price paths."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.portfolio.sip_rules import (
    SipRule,
    SipRuleKind,
    compare_rules,
    simulate_sip,
    summarize_rule_comparison,
)


def _prices(values: np.ndarray, start: str = "2015-01-01") -> pd.Series:
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)))


def test_flat_market_returns_exactly_what_was_invested():
    prices = _prices(np.full(800, 100.0))
    out = simulate_sip(prices, SipRule(SipRuleKind.CONTINUOUS), cash_rate_annual=0.0)
    assert out.final_value == pytest.approx(out.invested)
    assert out.xirr == pytest.approx(0.0, abs=1e-9)
    assert out.months_paused == 0


def test_trend_pause_in_a_steady_uptrend_sits_in_cash_and_lags():
    prices = _prices(100 * 1.001 ** np.arange(1500))
    base = simulate_sip(prices, SipRule(SipRuleKind.CONTINUOUS), cash_rate_annual=0.0)
    paused = simulate_sip(prices, SipRule(SipRuleKind.PAUSE_ABOVE_TREND, threshold=0.05), cash_rate_annual=0.0)
    assert paused.months_paused > 0
    assert paused.invested == base.invested
    assert paused.final_value < base.final_value


def test_dip_reserve_deploys_after_a_crash():
    path = np.concatenate([np.full(300, 100.0), np.full(300, 60.0), np.full(300, 100.0)])
    prices = _prices(path)
    out = simulate_sip(prices, SipRule(SipRuleKind.DIP_RESERVE, threshold=0.25, dip=0.2), cash_rate_annual=0.0)
    base = simulate_sip(prices, SipRule(SipRuleKind.CONTINUOUS), cash_rate_annual=0.0)
    assert out.final_value > base.final_value
    assert out.cash_left == pytest.approx(0.25 * 14)


def test_expensive_pe_pauses_once_percentile_history_exists():
    prices = _prices(np.full(1200, 100.0))
    pe = pd.Series(np.linspace(10, 30, 1200), index=prices.index)
    out = simulate_sip(prices, SipRule(SipRuleKind.PAUSE_EXPENSIVE, threshold=0.9), pe=pe, cash_rate_annual=0.0)
    assert out.months_paused > 0
    assert out.final_value == pytest.approx(out.invested)


def test_rolling_comparison_reports_every_rule_per_window():
    rng = np.random.default_rng(7)
    prices = _prices(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, 1600))))
    rules = [SipRule(SipRuleKind.CONTINUOUS), SipRule(SipRuleKind.PAUSE_AFTER_RALLY, threshold=0.2, label="rally")]
    windows = compare_rules(prices, rules, horizon_months=24, step_months=6)
    assert set(windows["rule"]) == {"continuous", "rally"}
    assert (windows.loc[windows["rule"] == "continuous", "vs_continuous"].abs() < 1e-12).all()
    summary = summarize_rule_comparison(windows)
    assert {"beat_rate", "median_edge", "worst_edge"} <= set(summary.columns)
