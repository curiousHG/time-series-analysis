"""Stock-screener constants: seed universe + display catalog config."""

from __future__ import annotations

# Nifty 50 (bare NSE symbols) — the seed batch we populate so the screener has data without
# scraping all ~2,372 listed names. The full universe lives in stock_registry (NSE master).
NIFTY_50 = (
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BPCL",
    "BHARTIARTL",
    "BRITANNIA",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "ITC",
    "INDUSINDBK",
    "INFY",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "LTIM",
    "M&M",
    "MARUTI",
    "NTPC",
    "NESTLEIND",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SBIN",
    "SUNPHARMA",
    "TCS",
    "TATACONSUM",
    "TATAMOTORS",
    "TATASTEEL",
    "TECHM",
    "TITAN",
    "ULTRACEMCO",
    "WIPRO",
    "SHRIRAMFIN",
    "ADANIGREEN",
)

# Alpha-categorisation thresholds (annualised Jensen alpha %, CAPM beta).
ALPHA_POSITIVE = 0.0
BETA_MARKET = 1.0


# Index tickers that aren't caught by the `^…` / `NIFTY …` (space) rules — the underscore/.NS
# benchmark forms from services.benchmarks. Kept here (not imported from services) to avoid a
# domain→services dependency.
_EXTRA_INDEX_SYMBOLS = frozenset({"NIFTY_MIDCAP_100.NS", "NIFTY_MIDCAP_150.NS", "NIFTY_SMLCAP_250.NS"})


def is_index_symbol(symbol: str) -> bool:
    """True for non-equity series fetched by a literal yfinance symbol — market indices
    (^NSEI, "NIFTY SMALLCAP 250") and FX rates (INR=X) — vs tradable NSE equities.

    These have no ISIN and live in index_ohlcv (fetched as-is, no `.NS` suffixing); equities
    live in stock_ohlcv. NSE equity symbols never start with '^', contain a space, or contain
    '=', so the rules are unambiguous.
    """
    return symbol.startswith(("^", "NIFTY ")) or "=" in symbol or symbol in _EXTRA_INDEX_SYMBOLS


def to_bare_symbol(symbol: str) -> str:
    """yfinance `RELIANCE.NS` → bare NSE `RELIANCE` (indices `^…` pass through)."""
    return symbol.removesuffix(".NS")


def to_yf_symbol(symbol: str) -> str:
    """Bare NSE `RELIANCE` → yfinance `RELIANCE.NS` (indices `^…` pass through)."""
    if symbol.endswith(".NS") or symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS"
