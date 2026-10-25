"""Indian-market benchmark catalogue and name-to-symbol resolution, shared by the fund Risk tab and the portfolio
scatter."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import polars as pl

BENCHMARK_SYMBOL_MAP: dict[str, str] = {
    "nifty 50": "^NSEI",
    "nifty50": "^NSEI",
    "nifty 100": "Nifty 100",
    "nifty 200": "Nifty 200",
    "nifty 500": "Nifty 500",
    "nifty next 50": "Nifty Next 50",
    "nifty midcap 50": "Nifty Midcap 50",
    "nifty midcap 100": "NIFTY Midcap 100",
    "nifty midcap 150": "Nifty Midcap 150",
    "nifty smallcap 100": "NIFTY Smallcap 100",
    "nifty smallcap 250": "Nifty Smallcap 250",
    "nifty bank": "Nifty Bank",
    "bank nifty": "Nifty Bank",
    "s&p bse sensex": "^BSESN",
    "bse sensex": "^BSESN",
    "sensex": "^BSESN",
    "nifty it": "Nifty IT",
    "nifty pharma": "Nifty Pharma",
    "nifty fmcg": "Nifty FMCG",
    "nifty auto": "Nifty Auto",
    "nifty metal": "Nifty Metal",
    "nifty energy": "Nifty Energy",
    "s&p 500": "^GSPC",
    "s&p500": "^GSPC",
    "nasdaq 100": "^NDX",
    "nasdaq100": "^NDX",
    "nasdaq": "^IXIC",
    "nasdaq composite": "^IXIC",
    "russell 3000": "^RUA",
    "russell 3000 growth": "^RAG",
    "russell 3000 value": "^RAV",
    "russell 1000": "^RUI",
    "russell 1000 growth": "^RLG",
    "russell 2000": "^RUT",
    "russell 2000 growth": "^RUO",
    "msci world": "URTH",
    "msci acwi": "ACWI",
    "msci all country world": "ACWI",
}

BENCHMARK_CHOICES: dict[str, str] = {
    "Nifty 50": "^NSEI",
    "Nifty 100": "Nifty 100",
    "Nifty 500": "Nifty 500",
    "Nifty Next 50": "Nifty Next 50",
    "Nifty Midcap 100": "NIFTY Midcap 100",
    "Nifty Midcap 150": "Nifty Midcap 150",
    "Nifty Smallcap 100": "NIFTY Smallcap 100",
    "Nifty Smallcap 250": "Nifty Smallcap 250",
    "BSE Sensex": "^BSESN",
    "Nifty Bank": "Nifty Bank",
    "Nifty IT": "Nifty IT",
    "S&P 500 (US)": "^GSPC",
    "Nasdaq 100 (US)": "^NDX",
    "Nasdaq Composite (US)": "^IXIC",
    "Russell 3000 (US)": "^RUA",
    "Russell 3000 Growth (US)": "^RAG",
    "Russell 2000 (US)": "^RUT",
    "MSCI World": "URTH",
    "MSCI ACWI": "ACWI",
}

DEFAULT_BENCHMARK_LABEL = "Nifty 50"

INDEX_DISPLAY_NAMES: dict[str, str] = {
    "^NSEI": "Nifty 50",
    "Nifty 100": "Nifty 100",
    "Nifty 200": "Nifty 200",
    "Nifty 500": "Nifty 500",
    "Nifty Next 50": "Nifty Next 50",
    "Nifty Midcap 50": "Nifty Midcap 50",
    "NIFTY Midcap 100": "Nifty Midcap 100",
    "NIFTY Smallcap 100": "Nifty Smallcap 100",
    "Nifty Bank": "Nifty Bank",
    "^BSESN": "BSE Sensex",
    "Nifty IT": "Nifty IT",
    "Nifty Pharma": "Nifty Pharma",
    "Nifty FMCG": "Nifty FMCG",
    "Nifty Auto": "Nifty Auto",
    "Nifty Metal": "Nifty Metal",
    "Nifty Energy": "Nifty Energy",
    "^GSPC": "S&P 500 (US)",
    "^NDX": "Nasdaq 100 (US)",
    "^IXIC": "Nasdaq Composite (US)",
    "^RUA": "Russell 3000 (US)",
    "^RAG": "Russell 3000 Growth (US)",
    "^RAV": "Russell 3000 Value (US)",
    "^RUI": "Russell 1000 (US)",
    "^RLG": "Russell 1000 Growth (US)",
    "^RUT": "Russell 2000 (US)",
    "^RUO": "Russell 2000 Growth (US)",
    "URTH": "MSCI World",
    "ACWI": "MSCI ACWI",
    "INR=X": "USD / INR",
}


def daily_returns_from_ohlcv(df: pl.DataFrame, name: str) -> pd.Series:
    """Close-to-close daily returns of an OHLCV frame as a pandas Series indexed by Date."""
    if df.is_empty():
        return pd.Series(dtype="float64", name=name)
    closes = df.select(["Date", "Close"]).to_pandas().set_index("Date").sort_index()["Close"]
    return closes.pct_change().dropna().rename(name)


def index_display_name(symbol: str) -> str:
    """Friendly label for an index/FX symbol; falls back to the raw symbol."""
    return INDEX_DISPLAY_NAMES.get(symbol, symbol)


SUBCATEGORY_BENCHMARK: dict[str, str] = {
    "Large Cap Fund": "Nifty 100",
    "Large & Mid Cap Fund": "Nifty 200",
    "Mid Cap Fund": "Nifty Midcap 150",
    "Small Cap Fund": "Nifty Smallcap 250",
    "Multi Cap Fund": "Nifty 500",
    "Flexi Cap Fund": "Nifty 500",
    "ELSS": "Nifty 500",
    "Focused Fund": "Nifty 500",
    "Value Fund": "Nifty 500",
    "Contra Fund": "Nifty 500",
    "Dividend Yield Fund": "Nifty 500",
    "Sectoral/ Thematic": "Nifty 500",
    "Aggressive Hybrid Fund": "Nifty 500",
    "Dynamic Asset Allocation or Balanced Advantage": "Nifty 500",
    "Multi Asset Allocation": "Nifty 500",
    "Equity Savings": "Nifty 500",
    "Retirement Fund": "Nifty 500",
    "Children's Fund": "Nifty 500",
    "Childrens Fund": "Nifty 500",
}


def subcategory_benchmark(sub_category: str | None) -> str | None:
    """Benchmark index symbol for a fund's SEBI sub-category (None = no equity benchmark)."""
    if not sub_category:
        return None
    return SUBCATEGORY_BENCHMARK.get(sub_category)


_DEBT_LIKE_KEYWORDS = (
    "liquid",
    "overnight",
    "money market",
    "duration",
    "bond",
    "gilt",
    "credit risk",
    "banking and psu",
    "floater",
    "arbitrage",
    "fixed maturity",
    "interval",
    "debt",
)


def _is_debt_like(sub_category: str | None) -> bool:
    s = (sub_category or "").lower()
    return any(k in s for k in _DEBT_LIKE_KEYWORDS)


def benchmark_for_fund(sub_category: str | None, metadata_benchmark: str | None = None) -> str | None:
    """Benchmark symbol for CAPM: mappable metadata benchmark, else sub-category benchmark, else Nifty 50.

    Debt-like funds with no mapped benchmark return None.
    """
    sym = resolve_benchmark_symbol(metadata_benchmark)
    if sym:
        return sym
    sym = subcategory_benchmark(sub_category)
    if sym:
        return sym
    if _is_debt_like(sub_category):
        return None
    return "^NSEI"


def _normalise_benchmark(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\b(tri|pri|total return( index)?|price return( index)?|index)\b", "", s)
    s = re.sub(r"[^a-z0-9& ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_PARENT_UNIVERSE = (
    ("smallcap", "Nifty Smallcap 250"),
    ("midcap", "Nifty Midcap 150"),
    ("next 50", "Nifty Next 50"),
    ("500", "Nifty 500"),
    ("200", "Nifty 200"),
    ("100", "Nifty 100"),
    ("50", "^NSEI"),
)


def _parent_universe_fallback(normalised: str) -> str | None:
    """Closest fetchable parent index for a Nifty strategy/factor benchmark (a proxy)."""
    if "nifty" not in normalised:
        return None
    for token, symbol in _PARENT_UNIVERSE:
        if token in normalised:
            return symbol
    return None


def resolve_benchmark_symbol(benchmark_name: str | None) -> str | None:
    """Map a metadata-benchmark string (e.g. 'NIFTY 500 TRI') to a fetchable symbol, or None."""
    if not benchmark_name:
        return None
    normalised = _normalise_benchmark(benchmark_name)
    return BENCHMARK_SYMBOL_MAP.get(normalised) or _parent_universe_fallback(normalised)
