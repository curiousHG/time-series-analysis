"""Portfolio alert rules for the Overview page.

Each alert rule is a pure function over preloaded frames; `build_alerts()` loads them and runs every rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import polars as pl

from core.timing import timeit
from data.repositories.amfi import load_amfi_df
from data.repositories.holdings import load_holdings
from data.repositories.scheme_metrics import load_metrics
from data.repositories.stock import ensure_stock_data
from data.repositories.tradebook import load_tradebook_from_db
from mutual_funds.display import make_slug, short_scheme_name
from mutual_funds.tradebook import normalize_transactions
from services.benchmarks import SUBCATEGORY_BENCHMARK
from services.mf.freshness import compute_holdings_freshness, compute_nav_freshness
from services.overview.market import trailing_return
from services.portfolio.analytics import fund_values_from_nav, fund_weights, lookthrough_exposure
from services.portfolio.value import build_portfolio_value_series, get_mapped_data

if TYPE_CHECKING:
    import pandas as pd

    from services.mf.freshness import FreshnessReport

Severity = Literal["info", "warn", "critical"]

UNDERPERF_6M_PP = 3.0
UNDERPERF_1Y_PP = 5.0
PORTFOLIO_DD_WARN = 0.10
PORTFOLIO_DD_CRIT = 0.20
STOCK_CONC_WARN_PCT = 8.0
STOCK_CONC_CRIT_PCT = 12.0
AMC_CONC_WARN_PCT = 40.0
FUND_CONC_WARN_PCT = 30.0

_CASH_PARKING_INSTRUMENTS = ("treps", "repo", "net current assets", "cash", "t-bill", "treasury bill")

_SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class Alert:
    severity: Severity
    kind: str
    message: str
    scheme_name: str | None = None
    value: float | None = None
    as_of: date | None = None


def sort_alerts(alerts: list[Alert]) -> list[Alert]:
    return sorted(alerts, key=lambda a: (_SEVERITY_ORDER[a.severity], a.kind))


def underperformance_alerts(
    metrics_df: pl.DataFrame,
    subcat_by_scheme: dict[str, str | None],
    bench_window_returns: dict[str, dict[str, float | None]],
    held: list[str],
) -> list[Alert]:
    """Held funds trailing their sub-category benchmark over 6M (warn) / 1Y (critical).

    :param metrics_df: cached `mf_scheme_metrics` frame (needs abs_return_6m/abs_return_1y).
    :param bench_window_returns: benchmark symbol → {"6m": ..., "1y": ...} decimal returns.
    """
    alerts: list[Alert] = []
    if metrics_df.is_empty():
        return alerts
    rows = {r["scheme_name"]: r for r in metrics_df.iter_rows(named=True)}
    for scheme in held:
        row = rows.get(scheme)
        symbol = SUBCATEGORY_BENCHMARK.get(subcat_by_scheme.get(scheme) or "")
        bench = bench_window_returns.get(symbol or "")
        if row is None or bench is None:
            continue
        short = short_scheme_name(scheme)
        gap_1y = _gap_pp(row.get("abs_return_1y"), bench.get("1y"))
        gap_6m = _gap_pp(row.get("abs_return_6m"), bench.get("6m"))
        if gap_1y is not None and gap_1y < -UNDERPERF_1Y_PP:
            alerts.append(
                Alert(
                    "critical",
                    "underperformance",
                    f"**{short}** is {-gap_1y:.1f} pp behind its benchmark over 1Y.",
                    scheme_name=scheme,
                    value=gap_1y,
                )
            )
        elif gap_6m is not None and gap_6m < -UNDERPERF_6M_PP:
            alerts.append(
                Alert(
                    "warn",
                    "underperformance",
                    f"**{short}** is {-gap_6m:.1f} pp behind its benchmark over 6M.",
                    scheme_name=scheme,
                    value=gap_6m,
                )
            )
    return alerts


def _gap_pp(fund_return: float | None, bench_return: float | None) -> float | None:
    """Fund-minus-benchmark gap in percentage points; None when either side is missing."""
    if fund_return is None or bench_return is None:
        return None
    return (fund_return - bench_return) * 100


def alpha_decay_alerts(
    metrics_df: pl.DataFrame,
    subcat_by_scheme: dict[str, str | None],
    held: list[str],
) -> list[Alert]:
    """Held funds with negative 1Y alpha despite a top-half long-run record in their sub-category."""
    alerts: list[Alert] = []
    if metrics_df.is_empty() or "rolling_3y_median" not in metrics_df.columns:
        return alerts

    subcat_df = pl.DataFrame({"scheme_name": list(subcat_by_scheme), "sub_category": list(subcat_by_scheme.values())})
    m = metrics_df.join(subcat_df, on="scheme_name", how="left")
    subcat_median = m.group_by("sub_category").agg(pl.col("rolling_3y_median").median().alias("subcat_median_3y"))
    m = m.join(subcat_median, on="sub_category", how="left")

    for row in m.filter(pl.col("scheme_name").is_in(held)).iter_rows(named=True):
        alpha = row.get("alpha_1y")
        r3y, med = row.get("rolling_3y_median"), row.get("subcat_median_3y")
        if alpha is None or r3y is None or med is None:
            continue
        if alpha < 0 and r3y >= med:
            alerts.append(
                Alert(
                    "warn",
                    "alpha_decay",
                    f"**{short_scheme_name(row['scheme_name'])}** has negative 1Y alpha "
                    f"({alpha * 100:+.1f}%) despite a top-half 3Y record in its sub-category.",
                    scheme_name=row["scheme_name"],
                    value=alpha,
                )
            )
    return alerts


def portfolio_drawdown_alert(pv: pd.Series) -> Alert | None:
    """Current drawdown of the portfolio value series from its running peak."""
    if pv is None or len(pv) < 2:
        return None
    peak = float(pv.cummax().iloc[-1])
    if peak <= 0:
        return None
    dd = 1.0 - float(pv.iloc[-1]) / peak
    if dd > PORTFOLIO_DD_CRIT:
        sev: Severity = "critical"
    elif dd > PORTFOLIO_DD_WARN:
        sev = "warn"
    else:
        return None
    return Alert(sev, "drawdown", f"Portfolio is **{dd * 100:.1f}%** below its all-time peak.", value=dd)


def concentration_alerts(
    exposure_df: pl.DataFrame,
    amc_weights: dict[str, float],
    fund_wts: dict[str, float],
) -> list[Alert]:
    """Look-through single-instrument, single-AMC and single-fund concentration."""
    alerts: list[Alert] = []

    if not exposure_df.is_empty():
        eq = exposure_df.filter(
            ~pl.col("instrument_name").str.to_lowercase().str.contains_any(list(_CASH_PARKING_INSTRUMENTS))
        )
        for row in eq.filter(pl.col("exposure_pct") > STOCK_CONC_WARN_PCT).iter_rows(named=True):
            sev: Severity = "critical" if row["exposure_pct"] > STOCK_CONC_CRIT_PCT else "warn"
            alerts.append(
                Alert(
                    sev,
                    "concentration",
                    f"Look-through: you hold **{row['exposure_pct']:.1f}%** of the portfolio in "
                    f"**{row['instrument_name']}** across {row['n_funds']} fund(s).",
                    value=row["exposure_pct"],
                )
            )

    for amc, w in amc_weights.items():
        if w * 100 > AMC_CONC_WARN_PCT:
            alerts.append(
                Alert(
                    "warn", "concentration", f"**{w * 100:.0f}%** of the portfolio sits with one AMC ({amc}).", value=w
                )
            )
    for fund, w in fund_wts.items():
        if w * 100 > FUND_CONC_WARN_PCT:
            alerts.append(
                Alert(
                    "warn",
                    "concentration",
                    f"**{short_scheme_name(fund)}** alone is **{w * 100:.0f}%** of the portfolio.",
                    scheme_name=fund,
                    value=w,
                )
            )
    return alerts


def staleness_alerts(nav_report: FreshnessReport, holdings_report: FreshnessReport) -> list[Alert]:
    """Wrap the data-freshness reports into info-level alerts."""
    alerts: list[Alert] = []
    if nav_report.has_stale:
        suffix = (
            f" (oldest {nav_report.max_business_days_old} business days behind)"
            if nav_report.max_business_days_old
            else ""
        )
        alerts.append(
            Alert(
                "info",
                "staleness",
                f"NAV data is stale for **{nav_report.stale_count} of {nav_report.total}** held funds{suffix}.",
                value=float(nav_report.stale_count),
            )
        )
    if holdings_report.has_stale:
        alerts.append(
            Alert(
                "info",
                "staleness",
                f"Holdings data is stale for **{holdings_report.stale_count} of {holdings_report.total}** held funds.",
                value=float(holdings_report.stale_count),
            )
        )
    return alerts


_WINDOW_TRADING_DAYS = {"6m": 126, "1y": 252}


def benchmark_window_returns(symbols: set[str]) -> dict[str, dict[str, float | None]]:
    """6M / 1Y trailing returns per benchmark symbol from stock_ohlcv (DB-first)."""
    out: dict[str, dict[str, float | None]] = {}
    start = datetime.now() - timedelta(days=500)
    end = datetime.now()
    for symbol in symbols:
        try:
            df = ensure_stock_data(symbol, start, end)
        except Exception:
            continue
        if df.is_empty():
            continue
        close = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()["Close"]
        out[symbol] = {w: trailing_return(close, n) for w, n in _WINDOW_TRADING_DAYS.items()}
    return out


@timeit("insights.build_alerts")
def build_alerts() -> list[Alert]:
    """Load portfolio state and run every alert rule. Returns severity-sorted alerts."""
    tradebook = load_tradebook_from_db()
    if tradebook.is_empty():
        return []
    result = get_mapped_data(normalize_transactions(tradebook))
    if result is None:
        return []
    mapped, portfolio_nav = result

    fund_values = fund_values_from_nav(mapped, portfolio_nav)
    held = sorted(fund_values)
    if not held:
        return []
    fund_wts = fund_weights(fund_values)
    slugs = [make_slug(n) for n in held]

    amfi = load_amfi_df().filter(pl.col("scheme_name").is_in(held))
    subcat_by_scheme: dict[str, str | None] = {
        r["scheme_name"]: r.get("sub_category") for r in amfi.iter_rows(named=True)
    }
    amc_by_scheme = {r["scheme_name"]: r.get("fund_house") for r in amfi.iter_rows(named=True)}
    amc_weights: dict[str, float] = {}
    for fund, w in fund_wts.items():
        amc = amc_by_scheme.get(fund)
        if amc:
            amc_weights[amc] = amc_weights.get(amc, 0.0) + w

    metrics_df = load_metrics(None)
    symbols = {
        SUBCATEGORY_BENCHMARK[sc]
        for sc in (subcat_by_scheme.get(n) for n in held)
        if sc and sc in SUBCATEGORY_BENCHMARK
    }
    bench_returns = benchmark_window_returns(symbols)

    alerts: list[Alert] = []
    alerts += underperformance_alerts(metrics_df, subcat_by_scheme, bench_returns, held)
    alerts += alpha_decay_alerts(metrics_df, subcat_by_scheme, held)

    pv_df = build_portfolio_value_series(mapped, portfolio_nav)
    if pv_df is not None and not pv_df.empty:
        dd_alert = portfolio_drawdown_alert(pv_df.set_index("date")["portfolio_value"])
        if dd_alert:
            alerts.append(dd_alert)

    exposure = lookthrough_exposure(load_holdings(slugs), fund_values)
    alerts += concentration_alerts(exposure, amc_weights, fund_wts)
    alerts += staleness_alerts(compute_nav_freshness(held, slugs), compute_holdings_freshness(held, slugs))
    return sort_alerts(alerts)
