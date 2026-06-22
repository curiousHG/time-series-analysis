"""Pure data assembly + filtering for the MF Screener.

No Streamlit imports here — UI caching is layered on top of these functions in
`ui/state/loaders.py`. Anything that does math or builds the screener DataFrame lives
in this module so it stays testable and reusable across pages.
"""

from __future__ import annotations

import logging
import re
from datetime import date as _date
from datetime import timedelta

import polars as pl

from data.repositories.screener import load_screener_view
from data.repositories.stock import ensure_stock_data
from mutual_funds.display import detect_option, detect_plan

logger = logging.getLogger("services.screener")


# ---- Status cell -----------------------------------------------------------------------


def status_cell(status: str | None) -> str:
    """Map a per-source status to a screener cell glyph: ✓ available, ✗ pending/missing, — untracked."""
    if status is None:
        return "—"
    if status == "available":
        return "✓"
    return "✗"


# ---- Screener DataFrame assembly -------------------------------------------------------


def build_screener_df() -> pl.DataFrame:
    """Build the full screener DataFrame.

    Single SELECT in `load_screener_view` does the AMFI x metadata x metrics x registry
    JOIN at the DB. We only add the derived `plan` / `option` columns here (cheap string
    parses over scheme_name).
    """
    df = load_screener_view()
    if df.is_empty():
        return df
    return df.with_columns(
        pl.col("scheme_name").map_elements(detect_plan, return_dtype=pl.Utf8).alias("plan"),
        pl.col("scheme_name").map_elements(detect_option, return_dtype=pl.Utf8).alias("option"),
    )


# ---- Filter application ----------------------------------------------------------------


def apply_name_filter(df: pl.DataFrame, name_query: str, *, column: str = "scheme_name") -> pl.DataFrame:
    """Filter rows whose `column` matches every whitespace-separated token in `name_query`.

    AND across tokens, case-insensitive substring per token. Tokens are regex-escaped so
    user input like parentheses is treated literally. Empty query returns `df` unchanged.
    """
    if not name_query:
        return df
    out = df
    for token in name_query.split():
        out = out.filter(pl.col(column).str.contains(f"(?i){re.escape(token)}"))
    return out


def apply_filters(
    df: pl.DataFrame,
    *,
    name_query: str,
    amcs: list[str],
    cats: list[str],
    plans: list[str],
    options: list[str],
    aum_min: float,
    ter_max: float,
    only_tracked: bool,
    only_untracked: bool = False,
    has_nav: bool,
    cagr_min: float | None = None,
    sharpe_min: float | None = None,
    dd_min: float | None = None,
) -> pl.DataFrame:
    """Sidebar filter pipeline. All inputs are optional (empty list / 0 = no constraint).

    `cagr_min` / `sharpe_min` / `dd_min` are only honoured when `has_nav=True` is set —
    those filters require the metric columns to be populated and would otherwise drop
    every row.
    """
    out = apply_name_filter(df, name_query)
    if amcs:
        out = out.filter(pl.col("fund_house").is_in(amcs))
    if cats:
        out = out.filter(pl.col("category").is_in(cats))
    if plans:
        out = out.filter(pl.col("plan").is_in(plans))
    if options:
        out = out.filter(pl.col("option").is_in(options))
    if aum_min > 0:
        out = out.filter(pl.col("aum_crores").fill_null(0) >= aum_min)
    if ter_max < 5.0:
        out = out.filter(pl.col("expense_ratio").fill_null(0) <= ter_max)
    if only_tracked:
        out = out.filter(pl.col("nav_status").is_not_null())
    if only_untracked:
        # Untracked = absent from `mf_registry` → left join leaves nav_status null.
        out = out.filter(pl.col("nav_status").is_null())
    if has_nav:
        out = out.filter(pl.col("cagr_1y").is_not_null())
        if cagr_min is not None:
            out = out.filter(pl.col("cagr_1y") * 100 >= cagr_min)
        if sharpe_min is not None:
            out = out.filter(pl.col("sharpe_1y") >= sharpe_min)
        if dd_min is not None:
            out = out.filter(pl.col("max_dd_1y") * 100 >= dd_min)
    return out


# ---- Benchmark helpers (used by the chart's IR-numerator axis) -------------------------


def nifty_1y_cagr() -> float | None:
    """Nifty 50 1Y annualised geometric return from `stock_ohlcv`. Returns None on missing data.

    Pure read — `ensure_stock_data` is DB-first, so this is one SELECT in the steady state.
    """
    try:
        end = _date.today()
        start = end - timedelta(days=400)
        df = ensure_stock_data("^NSEI", start, end)
    except Exception:
        logger.exception("Failed to load Nifty 50 for IR-numerator axis")
        return None
    if df.is_empty() or df.height < 200:
        return None
    pdf = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()
    closes = pdf["Close"].dropna()
    if len(closes) < 200:
        return None
    last_year = closes.iloc[-252:] if len(closes) >= 252 else closes
    n = len(last_year) - 1
    if n <= 0 or last_year.iloc[0] <= 0:
        return None
    return float((last_year.iloc[-1] / last_year.iloc[0]) ** (252 / n) - 1)
