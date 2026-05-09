"""Per-scheme risk/return metrics computed from NAV history.

Pure functions; no Streamlit. UI layer caches results with @st.cache_data.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import polars as pl
import quantstats as qs
from sqlmodel import select

from core.database import get_session
from core.models import MfNav

logger = logging.getLogger("services.mf_metrics")

# Conservative annual risk-free rate; quantstats expects daily.
RISK_FREE_ANNUAL = 0.06
RF_DAILY = RISK_FREE_ANNUAL / 252
TRADING_DAYS = 252


def nav_series(scheme_name: str) -> pd.Series:
    """Return the daily NAV series for a scheme as a date-indexed pd.Series (empty if none)."""
    with get_session() as session:
        rows = session.exec(
            select(MfNav.date, MfNav.nav).where(MfNav.scheme_name == scheme_name).order_by(MfNav.date)
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows})
    return s.sort_index()


# Backwards-compat alias for any external caller still using the old underscored name.
_nav_series = nav_series


def _windowed_cagr(nav: pd.Series, days: int) -> float | None:
    if len(nav) < days + 1:
        return None
    end = nav.iloc[-1]
    start = nav.iloc[-(days + 1)]
    if start <= 0 or end <= 0:
        return None
    n = len(nav.iloc[-(days + 1) :]) - 1
    if n <= 0:
        return None
    return float((end / start) ** (TRADING_DAYS / n) - 1)


def compute_metrics_for_scheme(scheme_name: str) -> dict | None:
    """Return a metrics dict, or None if there isn't enough history (< ~1Y)."""
    nav = nav_series(scheme_name)
    if len(nav) < TRADING_DAYS:
        return None

    returns = nav.pct_change().dropna()
    if returns.empty:
        return None

    last_year = returns.iloc[-TRADING_DAYS:]

    try:
        sharpe = float(qs.stats.sharpe(last_year, rf=RF_DAILY))
    except Exception:
        sharpe = math.nan
    try:
        sortino = float(qs.stats.sortino(last_year, rf=RF_DAILY))
    except Exception:
        sortino = math.nan
    try:
        vol = float(qs.stats.volatility(last_year))
    except Exception:
        vol = math.nan
    try:
        max_dd = float(qs.stats.max_drawdown(last_year))
    except Exception:
        max_dd = math.nan

    # All-time metrics from full NAV history
    all_time_high = float(nav.max())
    pct_from_ath = float(nav.iloc[-1] / all_time_high - 1) if all_time_high > 0 else math.nan
    try:
        max_dd_all = float(qs.stats.max_drawdown(returns))
    except Exception:
        max_dd_all = math.nan

    return {
        "scheme_name": scheme_name,
        "cagr_1y": _windowed_cagr(nav, TRADING_DAYS),
        "cagr_3y": _windowed_cagr(nav, TRADING_DAYS * 3),
        "cagr_5y": _windowed_cagr(nav, TRADING_DAYS * 5),
        "cagr_10y": _windowed_cagr(nav, TRADING_DAYS * 10),
        "vol_1y": vol,
        "sharpe_1y": sharpe,
        "sortino_1y": sortino,
        "max_dd_1y": max_dd,
        "max_dd_all": max_dd_all,
        "pct_from_ath": pct_from_ath,
        "last_nav": float(nav.iloc[-1]),
        "last_nav_date": nav.index[-1].date(),
        "history_days": len(nav),
    }


def compute_alpha_beta(
    fund_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    min_overlap: int = 60,
) -> dict | None:
    """Jensen alpha / beta of a return series against a benchmark return series.

    Mirrors the math in ui/views/mutual_fund.py:478-487:
      beta  = cov(fund, bench) / var(bench)
      alpha = mean(fund) - beta * mean(bench), annualised by TRADING_DAYS
      r2    = corr(fund, bench) ** 2

    Returns None when fewer than `min_overlap` aligned days exist or benchmark variance is zero.
    Both inputs must be daily returns (decimals, not %).
    """
    if fund_returns is None or benchmark_returns is None or fund_returns.empty or benchmark_returns.empty:
        return None
    common = pd.concat([fund_returns.rename("f"), benchmark_returns.rename("b")], axis=1, join="inner").dropna()
    if len(common) < min_overlap or common["b"].var() == 0:
        return None
    cov = common["f"].cov(common["b"])
    var = common["b"].var()
    beta = float(cov / var)
    alpha = float((common["f"].mean() - beta * common["b"].mean()) * TRADING_DAYS)
    r2 = float(common["f"].corr(common["b"]) ** 2)
    return {"alpha": alpha, "beta": beta, "r2": r2, "n_overlap": len(common)}


def compute_metrics_from_returns(returns: pd.Series) -> dict:
    """1Y CAGR/Vol/Sharpe/MaxDD from any daily-returns Series. Used for the portfolio aggregate.

    All values are NaN when the series is too short (<TRADING_DAYS). Errors from
    quantstats propagate — the caller is expected to surface them.
    """
    out = {"cagr_1y": math.nan, "vol_1y": math.nan, "sharpe_1y": math.nan, "max_dd_1y": math.nan}
    if returns is None or returns.empty:
        return out
    last_year = returns.iloc[-TRADING_DAYS:]
    if len(last_year) < TRADING_DAYS:
        return out
    out["vol_1y"] = float(qs.stats.volatility(last_year))
    out["sharpe_1y"] = float(qs.stats.sharpe(last_year, rf=RF_DAILY))
    out["max_dd_1y"] = float(qs.stats.max_drawdown(last_year))
    cum = (1.0 + last_year).prod()
    if cum > 0:
        n = len(last_year)
        out["cagr_1y"] = float(cum ** (TRADING_DAYS / n) - 1)
    return out


def compute_tracking_error(scheme_name: str, benchmark_returns: pd.Series, window: int = TRADING_DAYS) -> float | None:
    """Annualised tracking error — std-dev of (fund minus benchmark) daily returns over `window`.

    Pass `benchmark_returns` (a pre-loaded daily-return series); the caller resolves the benchmark.
    Returns None if there aren't enough overlapping days (< 60).
    """
    if benchmark_returns is None or benchmark_returns.empty:
        return None
    nav = nav_series(scheme_name)
    if len(nav) < window + 1:
        return None
    fund_ret = nav.pct_change().dropna().iloc[-window:]
    common = pd.concat([fund_ret.rename("f"), benchmark_returns.rename("b")], axis=1, join="inner").dropna()
    if len(common) < 60:
        return None
    diff = common["f"] - common["b"]
    return float(diff.std() * math.sqrt(TRADING_DAYS))


def compute_all_metrics() -> pl.DataFrame:
    """Compute metrics in parallel for every scheme that has NAV history."""
    from data.repositories.nav import count_distinct_nav_schemes  # noqa: F401  (signal: count check)

    with get_session() as session:
        names = list(session.exec(select(MfNav.scheme_name).distinct()).all())

    if not names:
        return pl.DataFrame(schema={"scheme_name": pl.Utf8})

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(compute_metrics_for_scheme, n): n for n in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                m = future.result()
                if m is not None:
                    rows.append(m)
            except Exception as e:
                logger.warning("Metric compute failed for %s: %s", name, e)

    if not rows:
        return pl.DataFrame(schema={"scheme_name": pl.Utf8})
    return pl.DataFrame(rows)
