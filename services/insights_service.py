"""Insights service — alert rules, market pulse, and movers for the Overview page.

Each alert rule is a pure function over preloaded frames (unit-testable with synthetic
data); `build_alerts()` orchestrates the loading and runs every rule. Thresholds live as
module constants so they are tweakable in one place.
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
from services.data_freshness import compute_holdings_freshness, compute_nav_freshness
from services.portfolio_analytics import fund_values_from_nav, fund_weights, lookthrough_exposure
from services.portfolio_service import build_portfolio_value_series, get_mapped_data

if TYPE_CHECKING:
    import pandas as pd

    from services.data_freshness import FreshnessReport

Severity = Literal["info", "warn", "critical"]

# ---- Alert thresholds ------------------------------------------------------------------
UNDERPERF_6M_PP = 3.0  # warn when a fund trails its benchmark by > 3 pp over 6M
UNDERPERF_1Y_PP = 5.0  # critical when trailing by > 5 pp over 1Y
PORTFOLIO_DD_WARN = 0.10
PORTFOLIO_DD_CRIT = 0.20
STOCK_CONC_WARN_PCT = 8.0  # look-through single-instrument share of portfolio
STOCK_CONC_CRIT_PCT = 12.0
AMC_CONC_WARN_PCT = 40.0
FUND_CONC_WARN_PCT = 30.0

# Instruments that represent cash parking, not a concentration risk.
_CASH_LIKE = ("treps", "repo", "net current assets", "cash", "t-bill", "treasury bill")

_SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class Alert:
    severity: Severity
    kind: str  # "underperformance" | "alpha_decay" | "drawdown" | "concentration" | "staleness"
    message: str
    scheme_name: str | None = None
    value: float | None = None
    as_of: date | None = None


def sort_alerts(alerts: list[Alert]) -> list[Alert]:
    return sorted(alerts, key=lambda a: (_SEVERITY_ORDER[a.severity], a.kind))


# ---- Pure rules ------------------------------------------------------------------------


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
    """Held funds with negative 1Y alpha despite a top-half long-run record in their
    sub-category — the long-run consistency made them a pick; the recent alpha says the
    edge may be decaying."""
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
        eq = exposure_df.filter(~pl.col("instrument_name").str.to_lowercase().str.contains_any(list(_CASH_LIKE)))
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


# ---- Benchmark window returns ----------------------------------------------------------

_WINDOW_TRADING_DAYS = {"6m": 126, "1y": 252}


def trailing_return(close: pd.Series, rows_back: int) -> float | None:
    """Cumulative return over the trailing `rows_back` rows of a close series."""
    if len(close) < rows_back + 1:
        return None
    start, end = float(close.iloc[-(rows_back + 1)]), float(close.iloc[-1])
    if start <= 0:
        return None
    return end / start - 1


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


# ---- Orchestrator ----------------------------------------------------------------------


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

    metrics_df = load_metrics(None)  # full frame — alpha-decay ranks need the sub-category universe
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


# ---- Market pulse + movers -------------------------------------------------------------

MARKET_PULSE_SYMBOLS: dict[str, str] = {
    "^NSEI": "Nifty 50",
    "^BSESN": "Sensex",
    "^NSEBANK": "Nifty Bank",
    "^NSMIDCP": "Nifty Next 50",
}

# Broad + sectoral indices for the Overview sector board (all yfinance-fetchable). (symbol, label, group)
SECTOR_INDEX_SYMBOLS: list[tuple[str, str, str]] = [
    ("^NSEI", "Nifty 50", "Broad"),
    ("^NSMIDCP", "Nifty Next 50", "Broad"),
    ("NIFTY MIDCAP 150", "Nifty Midcap 150", "Broad"),
    ("NIFTY SMALLCAP 250", "Nifty Smallcap 250", "Broad"),
    ("^NSEBANK", "Bank", "Sector"),
    ("^CNXIT", "IT", "Sector"),
    ("^CNXPHARMA", "Pharma", "Sector"),
    ("^CNXAUTO", "Auto", "Sector"),
    ("^CNXFMCG", "FMCG", "Sector"),
    ("^CNXMETAL", "Metal", "Sector"),
    ("^CNXENERGY", "Energy", "Sector"),
    ("^CNXREALTY", "Realty", "Sector"),
    ("^CNXINFRA", "Infra", "Sector"),
    ("^CNXPSUBANK", "PSU Bank", "Sector"),
]

_SECTOR_WINDOWS = {"1D": 1, "1W": 5, "1M": 21, "3M": 63}


@timeit("insights.index_performance")
def index_performance() -> pl.DataFrame:
    """Trailing 1D/1W/1M/3M returns for broad + sectoral indices (DB-first via ensure_index_data).
    Powers the Overview 'what's leading/lagging by sector' board."""
    start = datetime.now() - timedelta(days=160)
    end = datetime.now()
    rows: list[dict] = []
    for symbol, label, group in SECTOR_INDEX_SYMBOLS:
        try:
            df = ensure_stock_data(symbol, start, end)  # delegates indices → index_ohlcv
        except Exception:
            continue
        if df.is_empty():
            continue
        close = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()["Close"]
        row = {"label": label, "group": group, "symbol": symbol}
        row.update({window: trailing_return(close, n) for window, n in _SECTOR_WINDOWS.items()})
        rows.append(row)
    return pl.DataFrame(rows) if rows else pl.DataFrame()


@timeit("insights.market_pulse")
def market_pulse(symbols: dict[str, str] | None = None) -> pl.DataFrame:
    """Last close + 1D/1W/1M/YTD change + 52-week position per index symbol."""
    symbols = symbols or MARKET_PULSE_SYMBOLS
    start = datetime.now() - timedelta(days=420)
    end = datetime.now()
    rows: list[dict] = []
    for symbol, label in symbols.items():
        try:
            df = ensure_stock_data(symbol, start, end)
        except Exception:
            continue
        if df.is_empty():
            continue
        pdf = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()
        close = pdf["Close"]
        last_date = pdf.index[-1]
        ytd_mask = pdf.index >= datetime(last_date.year, 1, 1)
        year_slice = close.iloc[-252:]
        lo52, hi52 = float(year_slice.min()), float(year_slice.max())
        rows.append(
            {
                "symbol": symbol,
                "label": label,
                "last_close": float(close.iloc[-1]),
                "as_of": last_date.date() if hasattr(last_date, "date") else last_date,
                "chg_1d": trailing_return(close, 1),
                "chg_1w": trailing_return(close, 5),
                "chg_1m": trailing_return(close, 21),
                "chg_ytd": trailing_return(close, int(ytd_mask.sum()) - 1) if ytd_mask.sum() > 1 else None,
                "pos_52w": (float(close.iloc[-1]) - lo52) / (hi52 - lo52) if hi52 > lo52 else None,
            }
        )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


@timeit("insights.fund_movers")
def fund_movers() -> pl.DataFrame:
    """1W / 1M NAV moves of the user's held funds, weighted context via portfolio share.

    Fund-level (not look-through stock-level) because OHLCV coverage for underlying
    holdings is thin; the panel labels this honestly.
    """
    tradebook = load_tradebook_from_db()
    if tradebook.is_empty():
        return pl.DataFrame()
    result = get_mapped_data(normalize_transactions(tradebook))
    if result is None:
        return pl.DataFrame()
    mapped, portfolio_nav = result
    fund_values = fund_values_from_nav(mapped, portfolio_nav)
    wts = fund_weights(fund_values)
    if not wts:
        return pl.DataFrame()

    nav_pd = portfolio_nav.select(["date", "schemeName", "nav"]).to_pandas()
    rows: list[dict] = []
    for scheme, w in wts.items():
        s = nav_pd[nav_pd["schemeName"] == scheme].set_index("date")["nav"].astype(float).sort_index()
        rows.append(
            {
                "fund": short_scheme_name(scheme),
                "scheme_name": scheme,
                "weight_pct": w * 100,
                "chg_1w": trailing_return(s, 5),
                "chg_1m": trailing_return(s, 21),
            }
        )
    out = pl.DataFrame(rows)
    return out.sort(pl.col("chg_1w").abs(), descending=True, nulls_last=True)
