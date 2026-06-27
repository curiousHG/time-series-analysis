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
    "nifty midcap 150": "NIFTY_MIDCAP_150.NS",
    "nifty smallcap 100": "^CNXSC",
    "nifty smallcap 250": "NIFTY_SMLCAP_250.NS",
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
}

# Curated dropdown options for UI selectors. Order = display order.
BENCHMARK_CHOICES: dict[str, str] = {
    "Nifty 50": "^NSEI",
    "Nifty 100": "^CNX100",
    "Nifty 500": "^CRSLDX",
    "Nifty Next 50": "^NSMIDCP",
    "Nifty Midcap 100": "NIFTY_MIDCAP_100.NS",
    "Nifty Smallcap 100": "^CNXSC",
    "BSE Sensex": "^BSESN",
    "Nifty Bank": "^NSEBANK",
    "Nifty IT": "^CNXIT",
}

DEFAULT_BENCHMARK_LABEL = "Nifty 50"

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


def _normalise_benchmark(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\b(tri|pri|total return( index)?|price return( index)?|index)\b", "", s)
    s = re.sub(r"[^a-z0-9& ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_benchmark_symbol(benchmark_name: str | None) -> str | None:
    """Map a metadata-benchmark string (e.g. 'NIFTY 500 TRI') to a fetchable symbol."""
    if not benchmark_name:
        return None
    return BENCHMARK_SYMBOL_MAP.get(_normalise_benchmark(benchmark_name))
