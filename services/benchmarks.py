"""Indian-market benchmark catalogue and name→symbol resolution.

Single source of truth for both the single-fund Risk tab (mutual_fund.py) and the
portfolio Risk-vs-Return scatter — keeps the symbol map from drifting between callers.
"""

from __future__ import annotations

import re

# Map common Indian benchmark names → yfinance / jugaad symbols.
# Keys are matched after normalisation (lowercase, strip "TRI"/"Index"/"PRI"/"Total Return"/punctuation).
BENCHMARK_SYMBOL_MAP: dict[str, str] = {
    "nifty 50": "^NSEI",
    "nifty50": "^NSEI",
    "nifty 100": "^CNX100",
    "nifty 200": "^CNX200",
    "nifty 500": "^CRSLDX",
    "nifty next 50": "^NSMIDCP",
    "nifty midcap 50": "^NSEMDCP50",
    "nifty midcap 100": "NIFTY_MIDCAP_100.NS",
    # yfinance delisted NIFTY_MIDCAP_150.NS / NIFTY_SMLCAP_250.NS (0 rows since 2025) —
    # use the niftyindices source (space-form symbols route there in ensure_stock_data).
    "nifty midcap 150": "NIFTY MIDCAP 150",
    "nifty smallcap 100": "^CNXSC",
    "nifty smallcap 250": "NIFTY SMALLCAP 250",
    "nifty bank": "^NSEBANK",
    "bank nifty": "^NSEBANK",
    "s&p bse sensex": "^BSESN",
    "bse sensex": "^BSESN",
    "sensex": "^BSESN",
    "nifty it": "^CNXIT",
    "nifty pharma": "^CNXPHARMA",
    "nifty fmcg": "^CNXFMCG",
    "nifty auto": "^CNXAUTO",
    "nifty metal": "^CNXMETAL",
    "nifty energy": "^CNXENERGY",
    # US / global benchmarks — for overseas FoFs (e.g. Franklin U.S. Opportunities → Russell
    # 3000 Growth). All resolve on yfinance and route to index_ohlcv via the '^' convention.
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

# Curated dropdown options for UI selectors (index picker, risk-vs-return). Order = display.
BENCHMARK_CHOICES: dict[str, str] = {
    "Nifty 50": "^NSEI",
    "Nifty 100": "^CNX100",
    "Nifty 500": "^CRSLDX",
    "Nifty Next 50": "^NSMIDCP",
    "Nifty Midcap 100": "NIFTY_MIDCAP_100.NS",
    "Nifty Midcap 150": "NIFTY MIDCAP 150",
    "Nifty Smallcap 100": "^CNXSC",
    "Nifty Smallcap 250": "NIFTY SMALLCAP 250",
    "BSE Sensex": "^BSESN",
    "Nifty Bank": "^NSEBANK",
    "Nifty IT": "^CNXIT",
    # US / global (yfinance)
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

# symbol → friendly label, for index selectors that only know the raw symbol.
INDEX_DISPLAY_NAMES: dict[str, str] = {
    "^NSEI": "Nifty 50",
    "^CNX100": "Nifty 100",
    "^CNX200": "Nifty 200",
    "^CRSLDX": "Nifty 500",
    "^NSMIDCP": "Nifty Next 50",
    "^NSEMDCP50": "Nifty Midcap 50",
    "NIFTY_MIDCAP_100.NS": "Nifty Midcap 100",
    "NIFTY MIDCAP 150": "Nifty Midcap 150",
    "NIFTY_MIDCAP_150.NS": "Nifty Midcap 150 (legacy)",
    "^CNXSC": "Nifty Smallcap 100",
    "NIFTY SMALLCAP 250": "Nifty Smallcap 250",
    "NIFTY_SMLCAP_250.NS": "Nifty Smallcap 250 (legacy)",
    "^NSEBANK": "Nifty Bank",
    "^BSESN": "BSE Sensex",
    "^CNXIT": "Nifty IT",
    "^CNXPHARMA": "Nifty Pharma",
    "^CNXFMCG": "Nifty FMCG",
    "^CNXAUTO": "Nifty Auto",
    "^CNXMETAL": "Nifty Metal",
    "^CNXENERGY": "Nifty Energy",
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


def index_display_name(symbol: str) -> str:
    """Friendly label for an index/FX symbol; falls back to the raw symbol."""
    return INDEX_DISPLAY_NAMES.get(symbol, symbol)

# SEBI sub-category → benchmark index symbol (fetchable via yfinance "^…" or niftyindices
# "NIFTY …"). Sub-categories absent here (all Debt, Arbitrage, Index/ETF/FoF, Conservative
# Hybrid) have no meaningful equity benchmark → CAPM alpha/beta is left NaN rather than
# computed against an equity index it has near-zero correlation with.
SUBCATEGORY_BENCHMARK: dict[str, str] = {
    # Equity — each maps to its SEBI benchmark (or the closest fetchable proxy).
    "Large Cap Fund": "^CNX100",  # Nifty 100
    "Large & Mid Cap Fund": "^CNX200",  # Nifty 200 (proxy for LargeMidcap 250)
    "Mid Cap Fund": "NIFTY MIDCAP 150",  # niftyindices (yfinance lacks it)
    "Small Cap Fund": "NIFTY SMALLCAP 250",  # niftyindices
    "Multi Cap Fund": "^CRSLDX",  # Nifty 500
    "Flexi Cap Fund": "^CRSLDX",
    "ELSS": "^CRSLDX",
    "Focused Fund": "^CRSLDX",
    "Value Fund": "^CRSLDX",
    "Contra Fund": "^CRSLDX",
    "Dividend Yield Fund": "^CRSLDX",
    "Sectoral/ Thematic": "^CRSLDX",  # broad fallback (sector index needs name parsing)
    # Hybrid / solution-oriented with material equity exposure — Nifty 500 is a rough lens.
    "Aggressive Hybrid Fund": "^CRSLDX",
    "Dynamic Asset Allocation or Balanced Advantage": "^CRSLDX",
    "Multi Asset Allocation": "^CRSLDX",
    "Equity Savings": "^CRSLDX",
    "Retirement Fund": "^CRSLDX",
    "Children's Fund": "^CRSLDX",
    "Childrens Fund": "^CRSLDX",
}


def subcategory_benchmark(sub_category: str | None) -> str | None:
    """Benchmark index symbol for a fund's SEBI sub-category (None = no equity benchmark)."""
    if not sub_category:
        return None
    return SUBCATEGORY_BENCHMARK.get(sub_category)


# Fixed-income / non-directional sub-categories where an equity CAPM benchmark is meaningless.
_DEBT_LIKE_KEYWORDS = (
    "liquid", "overnight", "money market", "duration", "bond", "gilt", "credit risk",
    "banking and psu", "floater", "arbitrage", "fixed maturity", "interval", "debt",
)


def _is_debt_like(sub_category: str | None) -> bool:
    s = (sub_category or "").lower()
    return any(k in s for k in _DEBT_LIKE_KEYWORDS)


def benchmark_for_fund(sub_category: str | None, metadata_benchmark: str | None = None) -> str | None:
    """Resolve a fund's benchmark index symbol for CAPM alpha/beta.

    Priority: (1) the fund's named metadata benchmark if mappable (e.g. "Russell 3000 Growth
    TRI" → ^RAG), (2) its SEBI sub-category benchmark, (3) Nifty 50 as the default for any
    non-debt fund. Debt / arbitrage / liquid schemes return None (equity CAPM is meaningless).
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


# Nifty strategy/factor indices (Quality / Value / Momentum / Alpha / Low-Vol …) aren't on
# yfinance and niftyindices no longer serves them cleanly — fall back to the closest fetchable
# PARENT universe. Longest tokens first so "500" doesn't match inside "50".
_PARENT_UNIVERSE = (
    ("smallcap", "NIFTY SMALLCAP 250"),
    ("midcap", "NIFTY MIDCAP 150"),
    ("next 50", "^NSMIDCP"),
    ("500", "^CRSLDX"),
    ("200", "^CNX200"),
    ("100", "^CNX100"),
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
    """Map a metadata-benchmark string (e.g. 'NIFTY 500 TRI') to a fetchable symbol. Nifty
    factor/strategy indices with no exact match fall back to their parent universe (a proxy)."""
    if not benchmark_name:
        return None
    normalised = _normalise_benchmark(benchmark_name)
    return BENCHMARK_SYMBOL_MAP.get(normalised) or _parent_universe_fallback(normalised)
