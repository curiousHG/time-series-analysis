"""Unit tests for services.insights_service alert rules (pure functions, synthetic frames)."""

from datetime import date

import pandas as pd
import polars as pl

from services.insights_service import (
    AMC_CONC_WARN_PCT,
    FUND_CONC_WARN_PCT,
    PORTFOLIO_DD_CRIT,
    PORTFOLIO_DD_WARN,
    STOCK_CONC_CRIT_PCT,
    STOCK_CONC_WARN_PCT,
    UNDERPERF_1Y_PP,
    UNDERPERF_6M_PP,
    alpha_decay_alerts,
    concentration_alerts,
    portfolio_drawdown_alert,
    sort_alerts,
    staleness_alerts,
    trailing_return,
    underperformance_alerts,
)


def _metrics(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_underperformance_thresholds():
    """Just-inside stays quiet; just-over trips warn (6M) and critical (1Y)."""
    bench = {"^CRSLDX": {"6m": 0.10, "1y": 0.20}}
    subcats = {"Fine": "Flexi Cap Fund", "Behind6m": "Flexi Cap Fund", "Behind1y": "Flexi Cap Fund"}
    metrics = _metrics(
        [
            # exactly at the 6M threshold — must NOT alert (rule is strictly greater)
            {"scheme_name": "Fine", "abs_return_6m": 0.10 - UNDERPERF_6M_PP / 100, "abs_return_1y": 0.20},
            {"scheme_name": "Behind6m", "abs_return_6m": 0.10 - (UNDERPERF_6M_PP + 0.5) / 100, "abs_return_1y": 0.20},
            {"scheme_name": "Behind1y", "abs_return_6m": 0.10, "abs_return_1y": 0.20 - (UNDERPERF_1Y_PP + 1) / 100},
        ]
    )
    alerts = underperformance_alerts(metrics, subcats, bench, ["Fine", "Behind6m", "Behind1y"])
    by_scheme = {a.scheme_name: a for a in alerts}
    assert "Fine" not in by_scheme
    assert by_scheme["Behind6m"].severity == "warn"
    assert by_scheme["Behind1y"].severity == "critical"


def test_underperformance_skips_unmapped_subcategory():
    metrics = _metrics([{"scheme_name": "Debt Fund", "abs_return_6m": -0.5, "abs_return_1y": -0.5}])
    alerts = underperformance_alerts(metrics, {"Debt Fund": "Liquid Fund"}, {}, ["Debt Fund"])
    assert alerts == []


def test_alpha_decay_flags_top_half_negative_alpha_only():
    subcats = dict.fromkeys(("Star", "Laggard", "PositiveAlpha"), "Flexi Cap Fund")
    metrics = _metrics(
        [
            {"scheme_name": "Star", "alpha_1y": -0.02, "rolling_3y_median": 0.18},  # top half + neg alpha → alert
            {"scheme_name": "Laggard", "alpha_1y": -0.05, "rolling_3y_median": 0.05},  # bottom half → no alert
            {"scheme_name": "PositiveAlpha", "alpha_1y": 0.03, "rolling_3y_median": 0.20},  # alpha fine → no alert
        ]
    )
    alerts = alpha_decay_alerts(metrics, subcats, ["Star", "Laggard", "PositiveAlpha"])
    assert [a.scheme_name for a in alerts] == ["Star"]
    assert alerts[0].severity == "warn"


def test_portfolio_drawdown_boundaries():
    peak_series = pd.Series([100.0, 120.0, 120.0 * (1 - PORTFOLIO_DD_WARN) + 0.01])
    assert portfolio_drawdown_alert(peak_series) is None  # a hair inside the warn threshold

    warn = portfolio_drawdown_alert(pd.Series([100.0, 120.0, 120.0 * (1 - PORTFOLIO_DD_WARN - 0.02)]))
    assert warn is not None and warn.severity == "warn"

    crit = portfolio_drawdown_alert(pd.Series([100.0, 120.0, 120.0 * (1 - PORTFOLIO_DD_CRIT - 0.02)]))
    assert crit is not None and crit.severity == "critical"


def test_concentration_alerts_instrument_amc_fund():
    exposure = pl.DataFrame(
        {
            "instrument_name": ["HDFC Bank", "TREPS", "Infosys"],
            "isin": ["INE040A01034", "", "INE009A01021"],
            "industry": ["Banks", None, "IT"],
            "market_cap": ["Large", None, "Large"],
            "asset_class": ["Equity", "Cash", "Equity"],
            "exposure_pct": [STOCK_CONC_CRIT_PCT + 1, 50.0, STOCK_CONC_WARN_PCT + 1],
            "n_funds": [3, 2, 1],
            "funds": [["A"], ["A"], ["B"]],
        }
    )
    alerts = concentration_alerts(
        exposure,
        amc_weights={"Big AMC": (AMC_CONC_WARN_PCT + 5) / 100, "Small AMC": 0.10},
        fund_wts={"Mega Fund - Growth": (FUND_CONC_WARN_PCT + 5) / 100, "Tiny Fund": 0.05},
    )
    kinds = [(a.severity, a.message) for a in alerts]
    assert len(alerts) == 4  # HDFC crit + Infosys warn + AMC warn + fund warn; TREPS excluded
    assert sum(1 for s, _ in kinds if s == "critical") == 1
    assert not any("TREPS" in m for _, m in kinds)


def test_staleness_alerts_wrap_reports():
    from services.data_freshness import FreshnessReport

    stale = FreshnessReport(
        current_date=date(2026, 7, 2), rows=[], stale_count=3, total=10, max_days_old=40, max_business_days_old=26
    )
    fresh = FreshnessReport(
        current_date=date(2026, 7, 2), rows=[], stale_count=0, total=10, max_days_old=None, max_business_days_old=None
    )
    alerts = staleness_alerts(stale, fresh)
    assert len(alerts) == 1
    assert alerts[0].severity == "info"
    assert "3 of 10" in alerts[0].message


def test_sort_alerts_severity_order():
    from services.insights_service import Alert

    alerts = sort_alerts(
        [
            Alert("info", "staleness", "c"),
            Alert("critical", "underperformance", "a"),
            Alert("warn", "alpha_decay", "b"),
        ]
    )
    assert [a.severity for a in alerts] == ["critical", "warn", "info"]


def test_trailing_return():
    import pytest

    s = pd.Series([100.0, 105.0, 110.0])
    assert trailing_return(s, 2) == pytest.approx(0.10)
    assert trailing_return(s, 5) is None
