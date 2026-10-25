"""Per-scheme risk/return metrics from NAV history: DB loading, computation and persistence."""

from __future__ import annotations

import datetime
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

import pandas as pd
import quantstats as qs
from sqlmodel import col, select

from core.database import get_session
from core.models import AmfiScheme, MfHolding, MfMetadata, MfNav
from core.timing import timed, timeit
from data.repositories.scheme_codes import resolve_codes
from data.repositories.scheme_metrics import clear_metrics, find_stale_schemes, load_metrics, upsert_metrics
from data.repositories.stock import ensure_stock_data, refresh_stock_to_today
from services.benchmarks import benchmark_for_fund, daily_returns_from_ohlcv
from services.constants import RF_DAILY, TRADING_DAYS
from services.mf.metrics_math import (
    absolute_return_or_nan,
    compute_alpha_beta,
    rolling_cagr_stats,
    safe_stat,
    windowed_cagr,
)
from services.price_adjust import adjust_splits

if TYPE_CHECKING:
    import polars as pl

logger = logging.getLogger(__name__)

_DEFUNCT_AFTER_DAYS = 270
_MAX_PLAUSIBLE_DAILY_RETURN = 1.0
_PERCENT_SCALE_MIN_TOTAL = 1.5


def nav_series(scheme_name: str) -> pd.Series:
    """Daily NAV as a date-indexed pd.Series, read by the resolved scheme_code (empty if none)."""
    code = resolve_codes([scheme_name]).get(scheme_name)
    with get_session() as session:
        stmt = select(MfNav.date, MfNav.nav).order_by(MfNav.date)
        if code is not None:
            stmt = stmt.where(MfNav.scheme_code == code)
        else:
            stmt = stmt.join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code).where(
                AmfiScheme.scheme_name == scheme_name
            )
        rows = session.exec(stmt).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows})
    return s.sort_index()


def _holdings_composition(scheme_name: str) -> dict[str, float]:
    """Composition + concentration weights from the latest mf_holdings snapshot; floats in [0, 1], NaN if absent."""
    nan = dict.fromkeys(("pct_equity", "pct_debt", "pct_cash", "pct_top3", "pct_top5", "pct_top10"), math.nan)  # fmt: skip
    with get_session() as session:
        code_row = session.exec(select(AmfiScheme.scheme_code).where(AmfiScheme.scheme_name == scheme_name)).first()
        if code_row is None:
            return nan
        code = int(code_row) if not isinstance(code_row, tuple) else int(code_row[0])
        rows = session.exec(
            select(MfHolding.weight, MfHolding.asset_class, MfHolding.portfolio_date)
            .where(MfHolding.scheme_code == code)
            .order_by(col(MfHolding.portfolio_date).desc(), col(MfHolding.weight).desc())
        ).all()
    if not rows:
        return nan

    latest_date = rows[0][2]
    latest = [r for r in rows if r[2] == latest_date and r[0] is not None]
    if not latest:
        return nan

    weights = [float(w) for (w, _, _) in latest]
    total = sum(weights) or 1.0
    weights = [w / 100.0 if total > _PERCENT_SCALE_MIN_TOTAL else w for w in weights]

    pct_equity = pct_debt = pct_cash = 0.0
    for w, ac, _ in latest:
        if ac is None:
            continue
        a = str(ac).lower()
        weight = (w / 100.0) if total > _PERCENT_SCALE_MIN_TOTAL else w
        if "equity" in a:
            pct_equity += weight
        elif "debt" in a or "bond" in a:
            pct_debt += weight
        elif "cash" in a or "money market" in a or "tbill" in a or "tri-party" in a:
            pct_cash += weight

    weights_sorted = sorted(weights, reverse=True)
    return {
        "pct_equity": pct_equity,
        "pct_debt": pct_debt,
        "pct_cash": pct_cash,
        "pct_top3": float(sum(weights_sorted[:3])),
        "pct_top5": float(sum(weights_sorted[:5])),
        "pct_top10": float(sum(weights_sorted[:10])),
    }


@timeit("mf_metrics.compute_metrics_for_scheme", slow_threshold_ms=50)
def compute_metrics_for_scheme(
    scheme_name: str,
    benchmark_returns: pd.Series | None = None,
) -> dict | None:
    """Metrics dict (keys mirror data.constants.METRIC_FIELDS + `scheme_name`), or None if < ~1Y history.

    `benchmark_returns` (e.g. Nifty 50 daily returns) populates alpha/beta/r2/tracking-error; NaN without it.
    """
    if "segregated" in scheme_name.lower():
        return None

    nav = nav_series(scheme_name)
    nav = nav[nav > 0]
    if len(nav) < TRADING_DAYS:
        return None
    if (datetime.date.today() - nav.index[-1].date()).days > _DEFUNCT_AFTER_DAYS:
        return None

    nav = adjust_splits(nav)
    returns = nav.pct_change().dropna()
    if returns.empty:
        return None

    bad = returns[returns.abs() > _MAX_PLAUSIBLE_DAILY_RETURN]
    if not bad.empty:
        logger.info(
            "'%s': dropping %d corrupt NAV return-day(s); worst %.0f%% on %s",
            scheme_name,
            len(bad),
            float(bad.loc[bad.abs().idxmax()]) * 100,
            bad.abs().idxmax().date(),
        )
        returns = returns.drop(bad.index)
        if len(returns) < TRADING_DAYS:
            return None

    last_year = returns.iloc[-TRADING_DAYS:]

    sharpe = safe_stat(qs.stats.sharpe, last_year, rf=RF_DAILY)
    sortino = safe_stat(qs.stats.sortino, last_year, rf=RF_DAILY)
    calmar = safe_stat(qs.stats.calmar, last_year)
    gain_to_pain = safe_stat(qs.stats.gain_to_pain_ratio, last_year)
    vol = safe_stat(qs.stats.volatility, last_year)
    negative_returns = last_year[last_year < 0]
    downside_vol = float(negative_returns.std() * math.sqrt(TRADING_DAYS)) if len(negative_returns) > 1 else math.nan

    max_dd = safe_stat(qs.stats.max_drawdown, last_year)
    cumulative_return_1y = float((1.0 + last_year).prod() - 1.0)
    avg_daily_return_1y = float(last_year.mean())

    pos = last_year[last_year > 0]
    neg = last_year[last_year < 0]
    win_rate = float(len(pos) / len(last_year)) if len(last_year) else math.nan
    best_day = safe_stat(qs.stats.best, last_year)
    worst_day = safe_stat(qs.stats.worst, last_year)
    var_95 = safe_stat(qs.stats.var, last_year)
    cvar_95 = safe_stat(qs.stats.cvar, last_year)
    skew = safe_stat(qs.stats.skew, last_year)
    kurt = safe_stat(qs.stats.kurtosis, last_year)

    kelly = safe_stat(qs.stats.kelly_criterion, last_year)
    avg_win = float(pos.mean()) if len(pos) else math.nan
    avg_loss = float(neg.mean()) if len(neg) else math.nan
    payoff_ratio = float(abs(avg_win / avg_loss)) if avg_loss and not math.isnan(avg_win) else math.nan

    all_time_high = float(nav.max())
    pct_from_ath = float(nav.iloc[-1] / all_time_high - 1) if all_time_high > 0 else math.nan
    max_dd_all = safe_stat(qs.stats.max_drawdown, returns)

    rolling_1y = rolling_cagr_stats(nav, TRADING_DAYS)
    rolling_3y = rolling_cagr_stats(nav, TRADING_DAYS * 3)
    rolling_5y = rolling_cagr_stats(nav, TRADING_DAYS * 5)

    abs_3m = absolute_return_or_nan(nav, TRADING_DAYS // 4)
    abs_6m = absolute_return_or_nan(nav, TRADING_DAYS // 2)
    abs_1y = absolute_return_or_nan(nav, TRADING_DAYS)

    composition = _holdings_composition(scheme_name)

    alpha = beta = r2 = tracking_err = math.nan
    if benchmark_returns is not None and not benchmark_returns.empty:
        ab = compute_alpha_beta(last_year, benchmark_returns)
        if ab is not None:
            alpha, beta, r2 = ab["alpha"], ab["beta"], ab["r2"]
        te = compute_tracking_error(scheme_name, benchmark_returns)
        if te is not None:
            tracking_err = te

    return {
        "scheme_name": scheme_name,
        "cagr_1y": windowed_cagr(nav, TRADING_DAYS),
        "cagr_3y": windowed_cagr(nav, TRADING_DAYS * 3),
        "cagr_5y": windowed_cagr(nav, TRADING_DAYS * 5),
        "cagr_10y": windowed_cagr(nav, TRADING_DAYS * 10),
        "vol_1y": vol,
        "downside_vol_1y": downside_vol,
        "sharpe_1y": sharpe,
        "sortino_1y": sortino,
        "calmar_1y": calmar,
        "gain_to_pain_1y": gain_to_pain,
        "max_dd_1y": max_dd,
        "cumulative_return_1y": cumulative_return_1y,
        "avg_daily_return_1y": avg_daily_return_1y,
        "win_rate_1y": win_rate,
        "best_day_1y": best_day,
        "worst_day_1y": worst_day,
        "var_95_1y": var_95,
        "cvar_95_1y": cvar_95,
        "skew_1y": skew,
        "kurt_1y": kurt,
        "kelly_1y": kelly,
        "avg_win_1y": avg_win,
        "avg_loss_1y": avg_loss,
        "payoff_ratio_1y": payoff_ratio,
        "max_dd_all": max_dd_all,
        "pct_from_ath": pct_from_ath,
        "abs_return_3m": abs_3m,
        "abs_return_6m": abs_6m,
        "abs_return_1y": abs_1y,
        "pct_equity": composition["pct_equity"],
        "pct_debt": composition["pct_debt"],
        "pct_cash": composition["pct_cash"],
        "pct_top3": composition["pct_top3"],
        "pct_top5": composition["pct_top5"],
        "pct_top10": composition["pct_top10"],
        "alpha_1y": alpha,
        "beta_1y": beta,
        "r2_1y": r2,
        "tracking_error_1y": tracking_err,
        "rolling_1y_min": rolling_1y["min"],
        "rolling_1y_median": rolling_1y["median"],
        "rolling_1y_mean": rolling_1y["mean"],
        "rolling_1y_max": rolling_1y["max"],
        "rolling_3y_min": rolling_3y["min"],
        "rolling_3y_median": rolling_3y["median"],
        "rolling_3y_mean": rolling_3y["mean"],
        "rolling_3y_max": rolling_3y["max"],
        "rolling_5y_min": rolling_5y["min"],
        "rolling_5y_median": rolling_5y["median"],
        "rolling_5y_mean": rolling_5y["mean"],
        "rolling_5y_max": rolling_5y["max"],
        "inception_date": nav.index[0].date(),
        "last_nav": float(nav.iloc[-1]),
        "last_nav_date": nav.index[-1].date(),
        "history_days": len(nav),
    }


def compute_tracking_error(scheme_name: str, benchmark_returns: pd.Series, window: int = TRADING_DAYS) -> float | None:
    """Annualised tracking error: std-dev of (fund - benchmark) daily returns; None if < 60 overlapping days."""
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


def _load_benchmark_returns(symbol: str) -> pd.Series:
    """Daily returns for a benchmark index from stock_ohlcv, force-refreshed to today."""
    try:
        today, db_max = refresh_stock_to_today(symbol)
        if db_max is None:
            logger.warning("Benchmark %s refresh returned no rows — CAPM stats vs it will be NaN", symbol)
            return pd.Series(dtype="float64")
        if db_max < today:
            logger.info(
                "Benchmark %s %d day(s) behind today (weekend/holiday) — using last available",
                symbol,
                (today - db_max).days,
            )
    except Exception:
        logger.exception("Forced refresh of benchmark %s failed — using whatever's in DB", symbol)

    end = pd.Timestamp.today().to_pydatetime()
    start = (pd.Timestamp.today() - pd.DateOffset(years=10)).to_pydatetime()
    try:
        df = ensure_stock_data(symbol, start, end)
    except Exception:
        logger.exception("Failed to load benchmark %s — alpha/beta/TE will be NaN", symbol)
        return pd.Series(dtype="float64")
    return daily_returns_from_ohlcv(df, symbol)


def _subcategories(scheme_names: list[str]) -> dict[str, str | None]:
    """scheme_name -> AMFI sub_category, for choosing each fund's benchmark."""
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_name, AmfiScheme.sub_category).where(col(AmfiScheme.scheme_name).in_(scheme_names))
        ).all()
    return {r[0]: r[1] for r in rows}


def _metadata_benchmarks(scheme_names: list[str]) -> dict[str, str | None]:
    """scheme_name -> its named metadata benchmark (e.g. 'Russell 3000 Growth TRI'), when known."""
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_name, MfMetadata.benchmark)
            .join(MfMetadata, MfMetadata.scheme_code == AmfiScheme.scheme_code)
            .where(col(AmfiScheme.scheme_name).in_(scheme_names))
        ).all()
    return {r[0]: r[1] for r in rows if r[1]}


@timeit("mf_metrics.recompute_metrics")
def recompute_metrics(scheme_names: list[str] | None = None, *, max_workers: int = 4) -> int:
    """Recompute and persist metrics for `scheme_names` (or every scheme with NAV); returns rows upserted.

    Schemes that compute to None (corrupt NAV, short history) get their stale cache row evicted.
    """
    if scheme_names is None:
        with get_session() as session:
            scheme_names = list(
                session.exec(
                    select(AmfiScheme.scheme_name).join(MfNav, MfNav.scheme_code == AmfiScheme.scheme_code).distinct()
                ).all()
            )

    scheme_names = list(dict.fromkeys(scheme_names))
    if not scheme_names:
        return 0

    subcats = _subcategories(scheme_names)
    meta_bench = _metadata_benchmarks(scheme_names)
    bench_sym_by_name = {n: benchmark_for_fund(subcats.get(n), meta_bench.get(n)) for n in scheme_names}
    distinct_syms = sorted({s for s in bench_sym_by_name.values() if s})
    logger.info("recompute: %d funds across %d benchmark(s): %s", len(scheme_names), len(distinct_syms), distinct_syms)
    bench_cache = {sym: _load_benchmark_returns(sym) for sym in distinct_syms}

    def _bench_for(name: str) -> pd.Series | None:
        return bench_cache.get(bench_sym_by_name.get(name) or "")

    rows: list[dict] = []
    skipped: list[str] = []
    with (
        timed(f"mf_metrics.recompute_metrics.parallel(n={len(scheme_names)})"),
        ThreadPoolExecutor(max_workers=max_workers) as pool,
    ):
        futures = {pool.submit(compute_metrics_for_scheme, n, _bench_for(n)): n for n in scheme_names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                m = future.result()
                if m is None:
                    skipped.append(name)
                else:
                    rows.append(m)
            except Exception as e:
                logger.warning("Metric compute failed for %s: %s", name, e)
                skipped.append(name)

    upserted = upsert_metrics(rows)
    if skipped:
        evicted = clear_metrics(skipped)
        logger.info("Evicted %d stale cache row(s) for %d skipped scheme(s)", evicted, len(skipped))
    return upserted


@timeit("mf_metrics.recompute_stale")
def recompute_stale_metrics(*, max_workers: int = 4) -> int:
    """Recompute metrics only for schemes whose cache is older than the latest NAV (daily cron / post-sync hook)."""
    stale = find_stale_schemes()
    if not stale:
        return 0
    logger.info("Recomputing metrics for %d stale scheme(s)", len(stale))
    return recompute_metrics(stale, max_workers=max_workers)


def load_cached_metrics(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Read pre-computed metrics from mf_scheme_metrics — single SELECT, no quantstats."""
    return load_metrics(scheme_names)
