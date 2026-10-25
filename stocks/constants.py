"""Stock-screener constants: seed universe + display catalog config."""

from __future__ import annotations

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

ALPHA_POSITIVE = 0.0
BETA_MARKET = 1.0


NSE_EXCHANGES = frozenset({"NSE", "NSI", "BSE", "BO"})


def is_nse_exchange(exchange: str | None) -> bool:
    """True when a registry exchange value denotes the Indian market (None defaults to NSE —
    the registry's NSE-master rows historically carried no exchange)."""
    return (exchange or "NSE").upper() in NSE_EXCHANGES


_EXTRA_INDEX_SYMBOLS = frozenset({"URTH", "ACWI"})


def is_index_symbol(symbol: str) -> bool:
    """True for symbols that live in index_ohlcv: market indices, NSE index names (they contain a space), FX
    rates and the MSCI ETF proxies. Equities live in stock_ohlcv."""
    return symbol.startswith("^") or " " in symbol or "=" in symbol or symbol in _EXTRA_INDEX_SYMBOLS


def to_bare_symbol(symbol: str) -> str:
    """yfinance `RELIANCE.NS` → bare NSE `RELIANCE` (indices `^…` pass through)."""
    return symbol.removesuffix(".NS")


def to_yf_symbol(symbol: str) -> str:
    """Bare NSE `RELIANCE` → yfinance `RELIANCE.NS` (indices `^…` pass through)."""
    if symbol.endswith(".NS") or symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS"
