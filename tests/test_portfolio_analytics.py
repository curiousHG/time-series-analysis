"""Unit tests for services.portfolio_analytics — XIRR and look-through exposure."""

from datetime import date

import pandas as pd
import polars as pl
import pytest

from services.portfolio_analytics import (
    compute_xirr,
    contribution_analysis,
    fund_values_from_nav,
    fund_weights,
    lookthrough_exposure,
)


def test_xirr_single_flow_one_year():
    """₹100 invested exactly one year ago, worth ₹110 today → 10% XIRR."""
    flows = pd.DataFrame({"date": [date(2024, 1, 1)], "amount": [100.0]})
    xirr = compute_xirr(flows, terminal_value=110.0, terminal_date=date(2025, 1, 1))
    assert xirr == pytest.approx(0.10, abs=2e-3)  # 365 vs 365.25 day-count slack


def test_xirr_sip_known_value():
    """Monthly ₹1000 SIP for 12 months ending worth 13,000 — XIRR must satisfy NPV = 0."""
    dates = pd.date_range("2024-01-01", periods=12, freq="MS").date
    flows = pd.DataFrame({"date": dates, "amount": [1000.0] * 12})
    terminal_date = date(2025, 1, 1)
    xirr = compute_xirr(flows, terminal_value=13_000.0, terminal_date=terminal_date)
    assert xirr is not None

    # Future-value form: each SIP instalment compounded from its date to the valuation date.
    years = [(pd.Timestamp(terminal_date) - pd.Timestamp(d)).days / 365.25 for d in dates]
    npv = sum(-1000.0 * (1 + xirr) ** t for t in years) + 13_000.0
    assert npv == pytest.approx(0.0, abs=1e-6)
    assert 0.10 < xirr < 0.25  # ~8.3% absolute gain on a 12-month SIP ≈ mid-teens annualised


def test_xirr_empty_flows_returns_none():
    assert compute_xirr(pd.DataFrame(columns=["date", "amount"]), 100.0, date(2025, 1, 1)) is None


def test_xirr_clips_flows_after_valuation_date():
    """With stale NAV, a recent buy's units sit in terminal_value at par — its flow is
    treated as invested on the valuation date (no growth attributed), not compounded."""
    flows = pd.DataFrame({"date": [date(2024, 1, 1), date(2025, 6, 1)], "amount": [100.0, 500.0]})
    xirr = compute_xirr(flows, terminal_value=610.0, terminal_date=date(2025, 1, 1))
    # -100·(1+r) - 500 + 610 = 0  →  r = 10%
    assert xirr == pytest.approx(0.10, abs=2e-3)


def test_xirr_no_root_returns_none():
    """Only a post-valuation flow and terminal_value below it → NPV never crosses zero."""
    flows = pd.DataFrame({"date": [date(2025, 6, 1)], "amount": [100.0]})
    assert compute_xirr(flows, 50.0, date(2025, 1, 1)) is None


def _holdings_fixture() -> pl.DataFrame:
    """Two funds sharing one ISIN; weights are percent-of-fund."""
    return pl.DataFrame(
        {
            "schemeName": ["Fund A", "Fund A", "Fund B", "Fund B"],
            "instrumentName": ["HDFC Bank Ltd", "TCS Ltd", "HDFC Bank Limited", "Cash / TREPS"],
            "isin": ["INE040A01034", "INE467B01029", "INE040A01034", ""],
            "weight": [10.0, 5.0, 8.0, 4.0],
            "industry": ["Banks", "IT", "Banks", None],
            "marketCapBucket": ["Large", "Large", "Large", None],
            "assetClass": ["Equity", "Equity", "Equity", "Cash"],
        }
    )


def test_lookthrough_exposure_shared_isin_exact_pct():
    """60/40 fund split: HDFC Bank = 0.6x10% + 0.4x8% = 9.2% of the portfolio."""
    exposure = lookthrough_exposure(_holdings_fixture(), {"Fund A": 600.0, "Fund B": 400.0})
    hdfc = exposure.filter(pl.col("isin") == "INE040A01034").row(0, named=True)
    assert hdfc["exposure_pct"] == pytest.approx(9.2)
    assert hdfc["n_funds"] == 2
    tcs = exposure.filter(pl.col("isin") == "INE467B01029").row(0, named=True)
    assert tcs["exposure_pct"] == pytest.approx(3.0)  # 0.6 x 5%
    assert exposure["instrument_name"][0] == "HDFC Bank Ltd"  # sorted by exposure desc


def test_lookthrough_exposure_empty_inputs():
    assert lookthrough_exposure(_holdings_fixture(), {}).is_empty()
    empty = _holdings_fixture().clear()
    assert lookthrough_exposure(empty, {"Fund A": 100.0}).is_empty()


def test_fund_values_and_weights():
    mapped = pl.DataFrame(
        {
            "schemeName": ["Fund A", "Fund A", "Fund B"],
            "signed_qty": [10.0, -4.0, 5.0],
        }
    )
    nav = pl.DataFrame(
        {
            "schemeName": ["Fund A", "Fund A", "Fund B"],
            "date": [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 2)],
            "nav": [90.0, 100.0, 20.0],
        }
    )
    values = fund_values_from_nav(mapped, nav)
    assert values == {"Fund A": 600.0, "Fund B": 100.0}  # (10-4)x100, 5x20
    weights = fund_weights(values)
    assert weights["Fund A"] == pytest.approx(6 / 7)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_contribution_analysis_sums_to_portfolio_return():
    """Buy-and-hold, no intra-window flows: contributions sum exactly to portfolio return."""
    dates = pd.date_range("2024-01-01", periods=11, freq="D").date
    mapped = pl.DataFrame(
        {
            "schemeName": ["Fund A", "Fund B"],
            "trade_date": [dates[0], dates[0]],
            "signed_qty": [10.0, 20.0],
            "trade_value": [1000.0, 1000.0],
        }
    )
    # Fund A: 100 → 120 (+20%); Fund B: 50 → 45 (-10%). Start 2000, end 2100 → +5%.
    nav = pl.DataFrame(
        {
            "schemeName": ["Fund A"] * 11 + ["Fund B"] * 11,
            "date": list(dates) * 2,
            "nav": [100.0 + 2 * i for i in range(11)] + [50.0 - 0.5 * i for i in range(11)],
        }
    )
    contrib = contribution_analysis(mapped, nav, window_rows=10)
    assert len(contrib) == 2
    total = contrib["contribution_pct"].sum()
    assert total == pytest.approx(5.0)
    a = contrib[contrib["fund"] == "Fund A"].iloc[0]
    assert a["contribution_pct"] == pytest.approx(10.0)  # +200 on 2000 start
    assert a["fund_return_pct"] == pytest.approx(20.0)
    assert a["weight_start_pct"] == pytest.approx(50.0)


def test_contribution_analysis_short_history_empty():
    mapped = pl.DataFrame(
        {"schemeName": ["A"], "trade_date": [date(2024, 1, 1)], "signed_qty": [1.0], "trade_value": [100.0]}
    )
    nav = pl.DataFrame({"schemeName": ["A"], "date": [date(2024, 1, 1)], "nav": [100.0]})
    assert contribution_analysis(mapped, nav, window_rows=10).empty
