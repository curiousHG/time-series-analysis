import logging
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

logger = logging.getLogger("data.fetchers.stock")


def fetch_nse_equity_list() -> list[dict]:
    """Download the NSE equity master (EQUITY_L.csv) → [{symbol, name, isin, series}, ...]."""
    r = httpx.get(NSE_EQUITY_LIST_URL, headers=NSE_HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
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
    """The NSE-listed ETF universe from the live /api/etf endpoint → one dict per ETF with its
    classification key + display metadata. Underlying (`assets`), NAV, last price, 52-week range and
    30d/1Y performance all come in this single call. Returns [] on any failure (best-effort)."""

    def _f(v: object) -> float | None:
        try:
            return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
        except (TypeError, ValueError):
            return None

    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers=NSE_HEADERS) as client:
            client.get("https://www.nseindia.com/market-data/exchange-traded-funds-etf")  # prime cookies
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
    """All NSE index OHLCV for a single day in one download (ind_close_all) — 160+ indices incl.
    sectoral + factor/strategy indices (Quality/Value/Momentum/Alpha/Low-Vol), which yfinance and
    niftyindices don't serve. Returns Date/Name/Open/High/Low/Close/Volume (Name = NSE index name)."""
    from jugaad_data.nse import bhavcopy_index_raw  # noqa: PLC0415 — heavy optional dep, only on this path

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
    if "Index Name" not in df.columns or "Closing Index Value" not in df.columns:
        return None

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
    """Constituent symbols of an NSE index from its published `ind_<name>list.csv` (full official
    list — covers broad/sectoral indices incl. midcap/smallcap). Factor/strategy indices use
    irregular file names and 404 here; the caller falls back to screener.in. Returns [] on any miss."""
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
    if "Symbol" not in df.columns:
        return []
    return [str(s).strip() for s in df["Symbol"] if str(s).strip()]


def fetch_nifty500_symbols() -> list[str]:
    """Bare NSE symbols of the Nifty 500 constituents (from NSE's published index CSV)."""
    r = httpx.get(NIFTY500_LIST_URL, headers=NSE_HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    if "Symbol" not in df.columns:
        return []
    return [str(s).strip() for s in df["Symbol"] if str(s).strip()]


def query_stocks(query: str) -> pd.DataFrame:
    """Return equity symbols matching the query (any exchange — Indian or global), indexed by symbol.

    Defensive: yfinance's Lookup returns an empty, column-less frame for no-match / throttled
    responses (and can raise), so we normalise those to an empty result instead of a KeyError.
    """
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
    """One threaded yf.download for MANY tickers → {ticker: OHLCV frame}. Same auto-adjusted basis
    as fetch_symbol_data, so bulk daily refreshes land on the identical price basis as full-history
    fetches (mixing an unadjusted daily source with adjusted history drifts after every corporate
    action). Tickers with no rows in the window are omitted; `end_exclusive` follows yfinance's
    exclusive-end convention."""
    if not symbols:
        return {}
    try:
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
    if not isinstance(raw.columns, pd.MultiIndex):  # single ticker → flat columns
        df = raw.dropna(how="all")
        if not df.empty:
            out[symbols[0]] = df
        return out
    for ticker in raw.columns.get_level_values(0).unique():
        df = raw[ticker].dropna(how="all")
        if not df.empty:
            out[str(ticker)] = df
    return out


def fetch_symbol_data(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame | None:
    """Fetch historical data for a given stock symbol using yfinance."""
    try:
        # auto_adjust=True matches yfinance's new default (silences FutureWarning) and gives
        # split/dividend-adjusted closes, which is what we want for return calculations.
        data = yf.download(
            symbol,
            start=start,
            end=end,
            interval=interval,
            multi_level_index=False,
            auto_adjust=True,
            progress=False,
        )
        return data
    except Exception as e:
        logger.error(f"Error fetching data for symbol {symbol}: {e}")
        # Surface to the UI (drained as a toast); de-dup per symbol so repeats collapse.
        push_notice(f"Price fetch failed for {symbol}: {e}", level="error", key=f"fetch:{symbol}")
        return None


