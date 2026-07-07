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


# Index tickers that aren't caught by the `^…` / space / `=` rules — MSCI ETF proxies used as
# benchmark indices. Kept here (not imported from services) to avoid a domain→services dependency.
_EXTRA_INDEX_SYMBOLS = frozenset({"URTH", "ACWI"})


def is_index_symbol(symbol: str) -> bool:
    """True for non-equity series that live in index_ohlcv — market indices (^NSEI), NSE
    index-bhavcopy names ("Nifty Midcap 150" — any symbol containing a space, since NSE equity
    symbols never do), FX rates (INR=X), and the MSCI ETF benchmark proxies.

    These have no ISIN and are never `.NS`-suffixed; equities live in stock_ohlcv.
    """
    return symbol.startswith("^") or " " in symbol or "=" in symbol or symbol in _EXTRA_INDEX_SYMBOLS


def to_bare_symbol(symbol: str) -> str:
    """yfinance `RELIANCE.NS` → bare NSE `RELIANCE` (indices `^…` pass through)."""
    return symbol.removesuffix(".NS")


def to_yf_symbol(symbol: str) -> str:
    """Bare NSE `RELIANCE` → yfinance `RELIANCE.NS` (indices `^…` pass through)."""
    if symbol.endswith(".NS") or symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS"
