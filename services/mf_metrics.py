"""Per-scheme risk/return metrics from NAV history. Pure functions; UI caches the results."""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import polars as pl
import quantstats as qs
from sqlmodel import col, select

from core.database import get_session
from core.models import AmfiScheme, MfHolding, MfNav
from core.timing import timed, timeit
from data.repositories.scheme_metrics import clear_metrics, find_stale_schemes, load_metrics, upsert_metrics
from data.repositories.stock import ensure_stock_data, refresh_stock_to_today
from mutual_funds.display import make_slug  # noqa: F401 — back-compat re-export for callers
from services.constants import RF_DAILY, TRADING_DAYS

logger = logging.getLogger("services.mf_metrics")


def nav_series(scheme_name: str) -> pd.Series:
    """Daily NAV as a date-indexed pd.Series (empty if none).

    MfNav is keyed on scheme_code; JOIN to AmfiScheme to filter by name.
    """
    with get_session() as session:
        rows = session.exec(
            select(MfNav.date, MfNav.nav)
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .where(AmfiScheme.scheme_name == scheme_name)
            .order_by(MfNav.date)
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


def _rolling_cagr_stats(nav: pd.Series, window_days: int) -> dict[str, float]:
    """Min/median/mean/max of N-day rolling annualised CAGR over the fund's history.

    Describes the distribution of "if you'd held for N years ending on any day, what return".
    """
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


def _safe(fn, *args, **kwargs) -> float:
    """Call quantstats helper with NaN fallback. Errors don't break the bulk recompute loop."""
    try:
        v = fn(*args, **kwargs)
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else math.nan
    except Exception:
        return math.nan


def _absolute_return(nav: pd.Series, days_back: int) -> float:
    """Cumulative return from `days_back` ago to today. NaN if insufficient history."""
    if len(nav) < days_back + 1:
        return math.nan
    start = float(nav.iloc[-(days_back + 1)])
    end = float(nav.iloc[-1])
    if start <= 0 or end <= 0:
        return math.nan
    return end / start - 1.0


def _holdings_composition(scheme_name: str) -> dict[str, float]:
    """Composition + concentration weights from mf_holdings; floats in [0, 1], NaN if absent.

    Latest portfolio_date snapshot only — funds re-report holdings across dates; don't double-count.
    """
    nan = dict.fromkeys(("pct_equity", "pct_debt", "pct_cash", "pct_top3", "pct_top5", "pct_top10"), math.nan)  # fmt: skip
    # Phase 3: mf_holdings is keyed on scheme_code; resolve from scheme_name via AmfiScheme.
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

    # Restrict to the most-recent portfolio snapshot.
    latest_date = rows[0][2]
    latest = [r for r in rows if r[2] == latest_date and r[0] is not None]
    if not latest:
        return nan

    weights = [float(w) for (w, _, _) in latest]
    total = sum(weights) or 1.0  # AdvisorKhoj weights are usually already in % (sum~100)
    # Normalise: AdvisorKhoj sometimes reports 0-1, sometimes 0-100 — both end up in [0, 1].
    weights = [w / 100.0 if total > 1.5 else w for w in weights]

    # Asset-class buckets — the AdvisorKhoj `asset_class` field uses tags like "Equity",
    # "Debt", "Money Market", "Cash"… so we case-fold and substring match.
    pct_equity = pct_debt = pct_cash = 0.0
    for w, ac, _ in latest:
        if ac is None:
            continue
        a = str(ac).lower()
        weight = (w / 100.0) if total > 1.5 else w
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
    Skips funds with corrupt NAV (|daily return| > 100% — wound-up debt funds, side-pockets where MFAPI
    reports cumulative payouts) that would otherwise yield millions-of-percent volatility.
    """
    nav = nav_series(scheme_name)
    if len(nav) < TRADING_DAYS:
        return None

    returns = nav.pct_change().dropna()
    if returns.empty:
        return None

    # Sanity gate: real fund NAVs don't move >100% in a day. If we see one, the upstream
    # data is wrong (post-winding-up cumulative payouts, NAV-scale changes mid-series, etc.)
    # and every downstream stat will be garbage. Skip the row entirely.
    _CORRUPT_CUTOFF = 1.0
    bad = returns[returns.abs() > _CORRUPT_CUTOFF]
    if not bad.empty:
        worst_date = bad.abs().idxmax().date()
        worst_return = float(bad.loc[bad.abs().idxmax()])
        logger.warning(
            "Skipping '%s' — %d corrupt NAV day(s); worst on %s (%.0f%% single-day move)",
            scheme_name,
            len(bad),
            worst_date,
            worst_return * 100,
        )
        return None

    last_year = returns.iloc[-TRADING_DAYS:]

    # Risk-adjusted ratios
    sharpe = _safe(qs.stats.sharpe, last_year, rf=RF_DAILY)
    sortino = _safe(qs.stats.sortino, last_year, rf=RF_DAILY)
    calmar = _safe(qs.stats.calmar, last_year)
    gain_to_pain = _safe(qs.stats.gain_to_pain_ratio, last_year)
    vol = _safe(qs.stats.volatility, last_year)
    # Downside volatility — annualised stddev of negative-only daily returns. The Markowitz
    # frontier is intuited on (vol, return), but for asymmetric distributions downside vol
    # is the more honest risk axis since it ignores upside swings.
    _neg = last_year[last_year < 0]
    downside_vol = float(_neg.std() * math.sqrt(TRADING_DAYS)) if len(_neg) > 1 else math.nan

    # Drawdown / cumulative
    max_dd = _safe(qs.stats.max_drawdown, last_year)
    cumulative_return_1y = float((1.0 + last_year).prod() - 1.0)
    avg_daily_return_1y = float(last_year.mean())

    # Distribution stats
    pos = last_year[last_year > 0]
    neg = last_year[last_year < 0]
    win_rate = float(len(pos) / len(last_year)) if len(last_year) else math.nan
    best_day = _safe(qs.stats.best, last_year)
    worst_day = _safe(qs.stats.worst, last_year)
    var_95 = _safe(qs.stats.var, last_year)
    cvar_95 = _safe(qs.stats.cvar, last_year)
    skew = _safe(qs.stats.skew, last_year)
    kurt = _safe(qs.stats.kurtosis, last_year)

    # Position-sizing diagnostics
    kelly = _safe(qs.stats.kelly_criterion, last_year)
    avg_win = float(pos.mean()) if len(pos) else math.nan
    avg_loss = float(neg.mean()) if len(neg) else math.nan
    payoff_ratio = float(abs(avg_win / avg_loss)) if avg_loss and not math.isnan(avg_win) else math.nan

    # All-time
    all_time_high = float(nav.max())
    pct_from_ath = float(nav.iloc[-1] / all_time_high - 1) if all_time_high > 0 else math.nan
    max_dd_all = _safe(qs.stats.max_drawdown, returns)

    # Rolling annualised-CAGR distribution over 1/3/5-year windows.
    rolling_1y = _rolling_cagr_stats(nav, TRADING_DAYS)
    rolling_3y = _rolling_cagr_stats(nav, TRADING_DAYS * 3)
    rolling_5y = _rolling_cagr_stats(nav, TRADING_DAYS * 5)

    # Absolute (cumulative) returns over short windows.
    abs_3m = _absolute_return(nav, 63)  # ~3 months trading days
    abs_6m = _absolute_return(nav, 126)
    abs_1y = _absolute_return(nav, TRADING_DAYS)

    # Holdings composition + concentration (latest portfolio snapshot from mf_holdings).
    composition = _holdings_composition(scheme_name)

    # CAPM stats vs the supplied benchmark (Nifty 50 by convention from recompute_metrics).
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
        # CAGR
        "cagr_1y": _windowed_cagr(nav, TRADING_DAYS),
        "cagr_3y": _windowed_cagr(nav, TRADING_DAYS * 3),
        "cagr_5y": _windowed_cagr(nav, TRADING_DAYS * 5),
        "cagr_10y": _windowed_cagr(nav, TRADING_DAYS * 10),
        # Risk-adjusted ratios
        "vol_1y": vol,
        "downside_vol_1y": downside_vol,
        "sharpe_1y": sharpe,
        "sortino_1y": sortino,
        "calmar_1y": calmar,
        "gain_to_pain_1y": gain_to_pain,
        # Drawdown / cumulative
        "max_dd_1y": max_dd,
        "cumulative_return_1y": cumulative_return_1y,
        "avg_daily_return_1y": avg_daily_return_1y,
        # Distribution
        "win_rate_1y": win_rate,
        "best_day_1y": best_day,
        "worst_day_1y": worst_day,
        "var_95_1y": var_95,
        "cvar_95_1y": cvar_95,
        "skew_1y": skew,
        "kurt_1y": kurt,
        # Position sizing
        "kelly_1y": kelly,
        "avg_win_1y": avg_win,
        "avg_loss_1y": avg_loss,
        "payoff_ratio_1y": payoff_ratio,
        # All-time
        "max_dd_all": max_dd_all,
        "pct_from_ath": pct_from_ath,
        # Absolute returns
        "abs_return_3m": abs_3m,
        "abs_return_6m": abs_6m,
        "abs_return_1y": abs_1y,
        # Holdings composition + concentration
        "pct_equity": composition["pct_equity"],
        "pct_debt": composition["pct_debt"],
        "pct_cash": composition["pct_cash"],
        "pct_top3": composition["pct_top3"],
        "pct_top5": composition["pct_top5"],
        "pct_top10": composition["pct_top10"],
        # CAPM vs Nifty 50
        "alpha_1y": alpha,
        "beta_1y": beta,
        "r2_1y": r2,
        "tracking_error_1y": tracking_err,
        # Rolling-CAGR distribution
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
        # Provenance
        "inception_date": nav.index[0].date(),
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
    """Jensen alpha/beta of fund vs benchmark daily returns (decimals, not %).

    beta = cov/var, alpha = (mean_f - beta*mean_b) annualised, r2 = corr**2.
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
    """1Y CAGR/Vol/Sharpe/MaxDD from a daily-returns Series (portfolio aggregate).

    All NaN when the series is shorter than TRADING_DAYS. quantstats errors propagate to the caller.
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
    """Annualised tracking error — std-dev of (fund - benchmark) daily returns over `window`.

    None if fewer than 60 overlapping days.
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


def _load_nifty_for_recompute() -> pd.Series:
    """Nifty 50 daily returns from stock_ohlcv — the default benchmark.

    Force-refreshes to today first: `ensure_stock_data` skips sub-5-day gaps, which would
    silently leave the benchmark stale and push CAPM stats onto out-of-date data.
    """
    try:
        today, db_max = refresh_stock_to_today("^NSEI")
        if db_max is None:
            logger.warning("Nifty 50 refresh returned no rows — benchmark CAPM stats will be NaN")
            return pd.Series(dtype="float64")
        if db_max < today:
            logger.warning(
                "Nifty 50 still %d day(s) behind today after refresh (likely weekend/holiday); "
                "proceeding with last available data",
                (today - db_max).days,
            )
        else:
            logger.info("Nifty 50 benchmark fresh through %s", db_max)
    except Exception:
        logger.exception("Forced Nifty 50 refresh failed — falling back to whatever's in DB")

    end = pd.Timestamp.today().to_pydatetime()
    start = (pd.Timestamp.today() - pd.DateOffset(years=10)).to_pydatetime()
    try:
        df = ensure_stock_data("^NSEI", start, end)
    except Exception:
        logger.exception("Failed to load Nifty 50 for benchmark — alpha/beta/TE will be NaN")
        return pd.Series(dtype="float64")
    if df.is_empty():
        return pd.Series(dtype="float64")
    pdf = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()
    return pdf["Close"].pct_change().dropna().rename("nifty")


@timeit("mf_metrics.recompute_metrics")
def recompute_metrics(scheme_names: list[str] | None = None, *, max_workers: int = 4) -> int:
    """Recompute and persist metrics for `scheme_names` (or every scheme with NAV); returns rows upserted.

    Schemes that compute to None (corrupt NAV, short history) get their stale cache row evicted.
    """
    if scheme_names is None:
        # Phase 2: pull names via JOIN to amfi_schemes (MfNav no longer carries scheme_name).
        with get_session() as session:
            scheme_names = list(
                session.exec(
                    select(AmfiScheme.scheme_name).join(MfNav, MfNav.scheme_code == AmfiScheme.scheme_code).distinct()
                ).all()
            )

    if not scheme_names:
        return 0

    # Load Nifty 50 once and broadcast to every worker — saves N redundant DB reads.
    bench_returns = _load_nifty_for_recompute()

    rows: list[dict] = []
    skipped: list[str] = []
    with (
        timed(f"mf_metrics.recompute_metrics.parallel(n={len(scheme_names)})"),
        ThreadPoolExecutor(max_workers=max_workers) as pool,
    ):
        futures = {pool.submit(compute_metrics_for_scheme, n, bench_returns): n for n in scheme_names}
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
    # Evict stale rows for schemes that no longer pass the compute validation (e.g., the
    # corrupt-NAV guard). Without this, old runs' cached garbage would linger forever.
    if skipped:
        evicted = clear_metrics(skipped)
        logger.info("Evicted %d stale cache row(s) for %d skipped scheme(s)", evicted, len(skipped))
    return upserted


@timeit("mf_metrics.recompute_stale")
def recompute_stale_metrics(*, max_workers: int = 4) -> int:
    """Recompute metrics only for schemes whose cache is older than the latest NAV.

    Drives the daily cron and the post-NAV-sync hook — cheap when nothing changed.
    """
    stale = find_stale_schemes()
    if not stale:
        return 0
    logger.info("Recomputing metrics for %d stale scheme(s)", len(stale))
    return recompute_metrics(stale, max_workers=max_workers)


def load_cached_metrics(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Read pre-computed metrics from mf_scheme_metrics — single SELECT, no quantstats."""
    return load_metrics(scheme_names)
