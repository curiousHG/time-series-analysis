"""Market pulse, index performance and fund movers for the Overview page."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl

from core.timing import timeit
from data.repositories.stock import ensure_stock_data
from data.repositories.tradebook import load_tradebook_from_db
from mutual_funds.display import unique_short_names
from mutual_funds.tradebook import normalize_transactions
from services.portfolio.analytics import fund_values_from_nav, fund_weights
from services.portfolio.value import get_mapped_data

if TYPE_CHECKING:
    import pandas as pd


def trailing_return(close: pd.Series, rows_back: int) -> float | None:
    """Cumulative return over the trailing `rows_back` rows of a close series."""
    if len(close) < rows_back + 1:
        return None
    start, end = float(close.iloc[-(rows_back + 1)]), float(close.iloc[-1])
    if start <= 0:
        return None
    return end / start - 1


MARKET_PULSE_SYMBOLS: dict[str, str] = {
    "^NSEI": "Nifty 50",
    "^BSESN": "Sensex",
    "Nifty Bank": "Nifty Bank",
    "Nifty Next 50": "Nifty Next 50",
}

SECTOR_INDEX_SYMBOLS: list[tuple[str, str, str]] = [
    ("^NSEI", "Nifty 50", "Broad"),
    ("Nifty Next 50", "Nifty Next 50", "Broad"),
    ("Nifty Midcap 150", "Nifty Midcap 150", "Broad"),
    ("Nifty Smallcap 250", "Nifty Smallcap 250", "Broad"),
    ("Nifty Bank", "Bank", "Sector"),
    ("Nifty IT", "IT", "Sector"),
    ("Nifty Pharma", "Pharma", "Sector"),
    ("Nifty Auto", "Auto", "Sector"),
    ("Nifty FMCG", "FMCG", "Sector"),
    ("Nifty Metal", "Metal", "Sector"),
    ("Nifty Energy", "Energy", "Sector"),
    ("Nifty Realty", "Realty", "Sector"),
    ("Nifty Infrastructure", "Infra", "Sector"),
    ("Nifty PSU Bank", "PSU Bank", "Sector"),
]

INTERNATIONAL_INDEX_SYMBOLS: list[tuple[str, str, str]] = [
    ("^GSPC", "S&P 500", "US"),
    ("^NDX", "Nasdaq 100", "US"),
    ("^DJI", "Dow Jones", "US"),
    ("^FTSE", "FTSE 100", "UK"),
    ("^GDAXI", "DAX", "Germany"),
    ("^N225", "Nikkei 225", "Japan"),
    ("^HSI", "Hang Seng", "Hong Kong"),
]
INTERNATIONAL_PULSE_SYMBOLS: dict[str, str] = {s: label for s, label, _ in INTERNATIONAL_INDEX_SYMBOLS}

_SECTOR_WINDOWS = {"1D": 1, "1W": 5, "1M": 21, "3M": 63}


@timeit("insights.index_performance")
def index_performance() -> pl.DataFrame:
    """Trailing 1D/1W/1M/3M returns for broad + sectoral indices (DB-first via ensure_index_data)."""
    start = datetime.now() - timedelta(days=160)
    end = datetime.now()
    rows: list[dict] = []
    for symbol, label, group in SECTOR_INDEX_SYMBOLS:
        try:
            df = ensure_stock_data(symbol, start, end)
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
    """1W / 1M NAV moves of the user's held funds, with their portfolio share."""
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
    labels = unique_short_names(wts)
    rows: list[dict] = []
    for scheme, w in wts.items():
        s = nav_pd[nav_pd["schemeName"] == scheme].set_index("date")["nav"].astype(float).sort_index()
        rows.append(
            {
                "fund": labels[scheme],
                "scheme_name": scheme,
                "weight_pct": w * 100,
                "chg_1w": trailing_return(s, 5),
                "chg_1m": trailing_return(s, 21),
            }
        )
    out = pl.DataFrame(rows)
    return out.sort(pl.col("chg_1w").abs(), descending=True, nulls_last=True)
