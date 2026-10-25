import logging
import threading
from datetime import date
from io import StringIO

import httpx
import pandas as pd
import yfinance as yf

from core.notifications import push_notice
from data.constants import (
    NIFTY500_LIST_URL,
    NSE_EQUITY_LIST_URL,
    NSE_HEADERS,
)
from data.exceptions import UpstreamFormatError

logger = logging.getLogger("data.fetchers.stock")

_YF_LOCK = threading.Lock()


def _require_columns(df: pd.DataFrame, required: tuple[str, ...], source: str) -> None:
    """Raise `UpstreamFormatError` when a downloaded CSV is missing columns we read by name."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise UpstreamFormatError(source, f"missing column(s) {missing}; got {list(df.columns)}")


def fetch_nse_equity_list() -> list[dict]:
    """Download the NSE equity master (EQUITY_L.csv) → [{symbol, name, isin, series}, ...]."""
    r = httpx.get(NSE_EQUITY_LIST_URL, headers=NSE_HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    _require_columns(df, ("SYMBOL", "NAME OF COMPANY", "ISIN NUMBER", "SERIES"), "NSE EQUITY_L.csv")
    return [
        {
            "symbol": str(row["SYMBOL"]).strip(),
            "name": str(row["NAME OF COMPANY"]).strip(),
            "isin": str(row["ISIN NUMBER"]).strip(),
            "series": str(row["SERIES"]).strip(),
        }
        for _, row in df.iterrows()
    ]


def fetch_nse_etf_list() -> list[dict]:
    """NSE-listed ETFs from /api/etf → one dict per ETF (underlying, NAV, price, range, performance). [] on failure."""

    def _f(v: object) -> float | None:
        try:
            return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
        except (TypeError, ValueError):
            return None

    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers=NSE_HEADERS) as client:
            client.get("https://www.nseindia.com/market-data/exchange-traded-funds-etf")
            r = client.get("https://www.nseindia.com/api/etf")
            r.raise_for_status()
            rows = r.json().get("data", [])
    except Exception as e:
        logger.warning("NSE ETF list fetch failed: %s", e)
        return []
    out = []
    for x in rows:
        sym = str(x.get("symbol", "")).strip()
        if not sym:
            continue
        meta = x.get("meta") or {}
        out.append(
            {
                "symbol": sym,
                "name": str(meta.get("companyName") or x.get("assets") or sym).strip(),
                "underlying": str(x.get("assets") or "").strip() or None,
                "nav": _f(x.get("nav")),
                "ltp": _f(x.get("ltP")),
                "week_high": _f(x.get("wkhi")),
                "week_low": _f(x.get("wklo")),
                "change_30d_pct": _f(x.get("perChange30d")),
                "change_365d_pct": _f(x.get("perChange365d")),
                "qty": _f(x.get("qty")),
            }
        )
    return out


def fetch_nse_index_bhavcopy(day: date) -> pd.DataFrame | None:
    """All NSE index bars for one day (ind_close_all) → frame with Date/Name/OHLCV/valuation columns, or None."""
    from jugaad_data.nse import bhavcopy_index_raw  # noqa: PLC0415

    try:
        raw = bhavcopy_index_raw(day)
    except Exception as e:
        logger.debug("index bhavcopy unavailable for %s: %s", day, e)
        return None
    try:
        df = pd.read_csv(StringIO(raw))
    except Exception:
        return None
    df.columns = [c.strip() for c in df.columns]
    _require_columns(df, ("Index Name", "Index Date", "Closing Index Value"), "NSE index bhavcopy")

    def _num(colname: str) -> pd.Series:
        if colname not in df.columns:
            return pd.Series([None] * len(df))
        return pd.to_numeric(df[colname].astype(str).str.replace(",", "", regex=False), errors="coerce")

    out = pd.DataFrame(
        {
            "Date": pd.to_datetime(df["Index Date"], format="%d-%m-%Y", errors="coerce").dt.date,
            "Name": df["Index Name"].astype(str).str.strip(),
            "Open": _num("Open Index Value"),
            "High": _num("High Index Value"),
            "Low": _num("Low Index Value"),
            "Close": _num("Closing Index Value"),
            "Volume": _num("Volume"),
            "TurnoverCr": _num("Turnover (Rs. Cr.)"),
            "PE": _num("P/E"),
            "PB": _num("P/B"),
            "DivYield": _num("Div Yield"),
        }
    )
    return out[out["Close"].notna() & out["Date"].notna()]


def fetch_nse_index_constituents(index_name: str) -> list[str]:
    """Constituent symbols of an NSE index from its published `ind_<name>list.csv`; [] on any miss."""
    slug = "".join(c for c in index_name.lower() if c.isalnum())
    url = f"https://nsearchives.nseindia.com/content/indices/ind_{slug}list.csv"
    try:
        r = httpx.get(url, headers=NSE_HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
    except Exception as e:
        logger.debug("NSE constituents miss for %s (%s): %s", index_name, slug, e)
        return []
    df.columns = [c.strip() for c in df.columns]
    _require_columns(df, ("Symbol",), f"NSE constituents CSV (ind_{slug}list.csv)")
    return [str(s).strip() for s in df["Symbol"] if str(s).strip()]


def fetch_nifty500_symbols() -> list[str]:
    """Bare NSE symbols of the Nifty 500 constituents (from NSE's published index CSV)."""
    r = httpx.get(NIFTY500_LIST_URL, headers=NSE_HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    _require_columns(df, ("Symbol",), "NSE ind_nifty500list.csv")
    return [str(s).strip() for s in df["Symbol"] if str(s).strip()]


def search_global_tickers(query: str, *, max_results: int = 8) -> list[dict]:
    """Search Yahoo Finance for EQUITY/ETF tickers → [{symbol, name, exchange, quote_type}]; [] on failure.

    Falls back to yf.Lookup when yf.Search is throttled or empty.
    """
    try:
        quotes = yf.Search(query, max_results=max_results).quotes
    except Exception as e:
        logger.debug("yf.Search failed for %r: %s", query, e)
        quotes = []
    out = [
        {
            "symbol": str(q.get("symbol", "")).strip(),
            "name": str(q.get("shortname") or q.get("longname") or "").strip(),
            "exchange": str(q.get("exchDisp") or q.get("exchange") or "").strip(),
            "quote_type": str(q.get("quoteType") or "EQUITY").strip().upper(),
        }
        for q in quotes
        if q.get("symbol") and str(q.get("quoteType", "")).upper() in {"EQUITY", "ETF"}
    ]
    if out:
        return out
    lookup = query_stocks(query)
    return [
        {
            "symbol": str(sym),
            "name": str(row.get("shortName") or ""),
            "exchange": str(row.get("exchange") or ""),
            "quote_type": "EQUITY",
        }
        for sym, row in lookup.iterrows()
    ][:max_results]


def fetch_global_fundamentals(symbol: str) -> dict | None:
    """yfinance `.info` snapshot + quarterly income statement for an international ticker; None if unknown.

    Ratio fields are returned as percentages.
    """
    ticker = yf.Ticker(symbol)
    try:
        info = ticker.info or {}
    except Exception as e:
        logger.warning("yfinance info failed for %s: %s", symbol, e)
        return None
    if not info.get("longName") and not info.get("shortName") and info.get("regularMarketPrice") is None:
        return None

    quarters: list[dict] = []
    try:
        qis = ticker.quarterly_income_stmt
        if qis is not None and not qis.empty:

            def _at(row_name: str, quarter) -> float | None:
                if row_name not in qis.index:
                    return None
                v = qis.at[row_name, quarter]
                return float(v) if pd.notna(v) else None

            for q in sorted(qis.columns):
                revenue = _at("Total Revenue", q)
                op = _at("Operating Income", q)
                quarters.append(
                    {
                        "period_end": q.date() if hasattr(q, "date") else q,
                        "label": q.strftime("%b %Y") if hasattr(q, "strftime") else str(q),
                        "sales": revenue,
                        "net_profit": _at("Net Income", q),
                        "opm_pct": (op / revenue * 100) if op is not None and revenue else None,
                        "eps": _at("Diluted EPS", q),
                    }
                )
    except Exception as e:
        logger.debug("quarterly income stmt failed for %s: %s", symbol, e)

    def _pct(v: float | None) -> float | None:
        return v * 100 if v is not None else None

    return {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "currency": info.get("currency"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "pb": info.get("priceToBook"),
        "dividend_yield": info.get("dividendYield"),
        "roe": _pct(info.get("returnOnEquity")),
        "profit_margin": _pct(info.get("profitMargins")),
        "revenue_growth": _pct(info.get("revenueGrowth")),
        "beta": info.get("beta"),
        "week52_high": info.get("fiftyTwoWeekHigh"),
        "week52_low": info.get("fiftyTwoWeekLow"),
        "summary": info.get("longBusinessSummary"),
        "quarters": quarters,
    }


def query_stocks(query: str) -> pd.DataFrame:
    """Equity symbols matching the query (any exchange), indexed by symbol; empty frame on no match or failure."""
    empty = pd.DataFrame(columns=["shortName", "exchange", "quoteType"])
    empty.index.name = "symbol"
    try:
        df = yf.Lookup(query.lower()).all
    except Exception as e:
        logger.warning("yfinance Lookup failed for %r: %s", query, e)
        return empty
    if df is None or df.empty or "quoteType" not in df.columns:
        return empty
    return df[df["quoteType"] == "equity"]


def fetch_symbols_batch(symbols: list[str], start: date, end_exclusive: date) -> dict[str, pd.DataFrame]:
    """One threaded auto-adjusted yf.download for many tickers → {ticker: OHLCV frame}.

    Tickers with no rows are omitted; `end_exclusive` is yfinance's exclusive end date.
    Raises `UpstreamFormatError` if ticker attribution is lost.
    """
    if not symbols:
        return {}
    try:
        with _YF_LOCK:
            raw = yf.download(
                symbols,
                start=start,
                end=end_exclusive,
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
    except Exception as e:
        logger.error("batch price fetch failed for %d tickers: %s", len(symbols), e)
        push_notice(f"Batch price refresh failed: {e}", level="error", key="fetch:batch")
        return {}
    if raw is None or raw.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    if not isinstance(raw.columns, pd.MultiIndex):
        if len(symbols) != 1:
            raise UpstreamFormatError(
                "yfinance batch", f"flat frame for a {len(symbols)}-ticker request — ticker attribution lost"
            )
        df = raw.dropna(how="all")
        if not df.empty:
            out[symbols[0]] = df
        return out
    requested = set(symbols)
    for ticker in raw.columns.get_level_values(0).unique():
        if str(ticker) not in requested:
            raise UpstreamFormatError("yfinance batch", f"returned ticker {ticker!r} that was not requested")
        df = raw[ticker].dropna(how="all")
        if not df.empty:
            out[str(ticker)] = df
    return out


def _verified_single(data: pd.DataFrame | None, symbol: str) -> pd.DataFrame | None:
    """Flatten a one-ticker `group_by="ticker"` frame after checking it is for `symbol`."""
    if data is None or data.empty:
        return data
    if isinstance(data.columns, pd.MultiIndex):
        tickers = [str(t) for t in data.columns.get_level_values(0).unique()]
        if tickers != [symbol]:
            raise UpstreamFormatError("yfinance", f"asked for {symbol!r}, frame is for {tickers}")
        data = data[symbol]
    return data


def fetch_symbol_data(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame | None:
    """Auto-adjusted yfinance history for one symbol; None on failure."""
    try:
        with _YF_LOCK:
            data = yf.download(
                symbol,
                start=start,
                end=end,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                progress=False,
            )
        return _verified_single(data, symbol)
    except UpstreamFormatError:
        raise
    except Exception as e:
        logger.error(f"Error fetching data for symbol {symbol}: {e}")
        push_notice(f"Price fetch failed for {symbol}: {e}", level="error", key=f"fetch:{symbol}")
        return None
