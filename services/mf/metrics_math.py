"""Pure risk/return maths over NAV and return series passed in; no DB, no fetching."""

from __future__ import annotations

import math

import pandas as pd
import quantstats as qs

from services.constants import RF_DAILY, TRADING_DAYS


def windowed_cagr(nav: pd.Series, days: int) -> float | None:
    """Annualised CAGR over the trailing `days` NAV rows; None if insufficient history."""
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


def absolute_return(nav: pd.Series, days_back: int) -> float | None:
    """Cumulative return from `days_back` NAV rows ago to today; None if insufficient history."""
    v = absolute_return_or_nan(nav, days_back)
    return None if math.isnan(v) else v


def rolling_alpha(fund_returns: pd.Series, bench_returns: pd.Series, window: int = 126) -> pd.Series:
    """Rolling annualised Jensen's alpha of daily fund returns vs benchmark returns.

    beta_t = rolling cov(fund, bench) / rolling var(bench); alpha_t = (mean_f - beta_t *
    mean_b) * 252. Empty Series when the overlap is shorter than `window`.
    """
    aligned = pd.concat([fund_returns.rename("f"), bench_returns.rename("b")], axis=1, join="inner").dropna()
    if len(aligned) < window + 1:
        return pd.Series(dtype="float64")
    f, b = aligned["f"], aligned["b"]
    beta = f.rolling(window).cov(b) / b.rolling(window).var()
    alpha = (f.rolling(window).mean() - beta * b.rolling(window).mean()) * TRADING_DAYS
    return alpha.dropna()


def rolling_beat_rate(nav: pd.Series, bench_prices: pd.Series, window_days: int, min_windows: int = 30) -> float | None:
    """Share of rolling `window_days` windows where the fund's return beat the benchmark's.

    :param bench_prices: benchmark price/level series (any scale — the comparison is
        scale-invariant); reindexed to the NAV dates with forward-fill.
    """
    bench = bench_prices.reindex(nav.index).ffill()
    fund_rr = nav.pct_change(window_days, fill_method=None)
    bench_rr = bench.pct_change(window_days, fill_method=None)
    both = pd.concat([fund_rr.rename("f"), bench_rr.rename("b")], axis=1).dropna()
    if len(both) < min_windows:
        return None
    return float((both["f"] > both["b"]).mean())


def rolling_cagr_stats(nav: pd.Series, window_days: int) -> dict[str, float]:
    """Min/median/mean/max of N-day rolling annualised CAGR over the fund's history."""
    nan = {"min": math.nan, "median": math.nan, "mean": math.nan, "max": math.nan}
    if len(nav) < window_days + 1:
        return nan
    start = nav.shift(window_days)
    rolled = (nav / start) ** (TRADING_DAYS / window_days) - 1.0
    rolled = rolled.dropna()
    rolled = rolled[rolled.notna() & (start.reindex(rolled.index) > 0)]
    if rolled.empty:
        return nan
    return {
        "min": float(rolled.min()),
        "median": float(rolled.median()),
        "mean": float(rolled.mean()),
        "max": float(rolled.max()),
    }


def safe_stat(fn, *args, **kwargs) -> float:
    """Call quantstats helper with NaN fallback. Errors don't break the bulk recompute loop."""
    try:
        v = fn(*args, **kwargs)
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else math.nan
    except Exception:
        return math.nan


def absolute_return_or_nan(nav: pd.Series, days_back: int) -> float:
    """Cumulative return from `days_back` ago to today. NaN if insufficient history."""
    if len(nav) < days_back + 1:
        return math.nan
    start = float(nav.iloc[-(days_back + 1)])
    end = float(nav.iloc[-1])
    if start <= 0 or end <= 0:
        return math.nan
    return end / start - 1.0


def compute_alpha_beta(
    fund_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    min_overlap: int = 60,
) -> dict | None:
    """Jensen alpha/beta of fund vs benchmark daily returns (decimals).

    None when < `min_overlap` aligned days or benchmark variance is zero.
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
    """1Y CAGR/Vol/Sharpe/MaxDD from a daily-returns Series; all NaN when shorter than TRADING_DAYS."""
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


def portfolio_axis_metrics(returns: pd.Series) -> dict:
    """1Y metrics for an arbitrary daily-returns series, matching the fund-cache formulas.

    Covers every screener risk axis (vol/downside-vol/CVaR/max-DD) plus cagr_1y and
    sharpe_1y, so a portfolio point can sit on the same axes as the cached fund metrics.
    All NaN when the series is shorter than TRADING_DAYS.
    """
    out = dict.fromkeys(("cagr_1y", "vol_1y", "downside_vol_1y", "cvar_95_1y", "max_dd_1y", "sharpe_1y"), math.nan)
    if returns is None or returns.empty:
        return out
    last_year = returns.iloc[-TRADING_DAYS:]
    if len(last_year) < TRADING_DAYS:
        return out
    out.update(compute_metrics_from_returns(returns))
    neg = last_year[last_year < 0]
    out["downside_vol_1y"] = float(neg.std() * math.sqrt(TRADING_DAYS)) if len(neg) > 1 else math.nan
    out["cvar_95_1y"] = safe_stat(qs.stats.cvar, last_year)
    return out
