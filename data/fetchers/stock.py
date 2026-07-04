import json
import logging
from datetime import date, datetime, timedelta
from io import StringIO

import httpx
import pandas as pd
import yfinance as yf

from core.notifications import push_notice
from data.constants import (
    NIFTY500_LIST_URL,
    NIFTYINDICES_HEADERS,
    NIFTYINDICES_HISTORY_URL,
    NIFTYINDICES_PAGE_URL,
    NSE_EQUITY_LIST_URL,
    NSE_HEADERS,
)

logger = logging.getLogger("data.fetchers.stock")


def fetch_nse_index(name: str, start: date, end: date) -> pd.DataFrame | None:
    """Fetch a Nifty index's OHLC history from niftyindices.com (for indices yfinance lacks).

    `name` is the niftyindices index name, e.g. "NIFTY SMALLCAP 250". Returns a Date-indexed
    DataFrame (Open/High/Low/Close; Volume is None for indices), or None. Requests are chunked
    by year — the first full-history fetch is slow, but DB-first caching makes the rest
    incremental.
    """
    raw: list[dict] = []
    with httpx.Client(timeout=40, follow_redirects=True, headers=NIFTYINDICES_HEADERS) as client:
        try:
            client.get(NIFTYINDICES_PAGE_URL)  # prime ASP.NET session cookies
        except httpx.HTTPError:
            pass
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=364), end)
            cinfo = json.dumps(
                {
                    "name": name,
                    "startDate": cur.strftime("%d-%b-%Y"),
                    "endDate": chunk_end.strftime("%d-%b-%Y"),
                    "indexName": name,
                }
            )
            try:
                resp = client.post(NIFTYINDICES_HISTORY_URL, json={"cinfo": cinfo})
                resp.raise_for_status()
                raw.extend(json.loads(resp.json()["d"]))
            except (httpx.HTTPError, KeyError, ValueError) as e:
                logger.warning("niftyindices fetch failed for %s %s..%s: %s", name, cur, chunk_end, e)
            cur = chunk_end + timedelta(days=1)

    records = []
    for row in raw:
        try:
            records.append(
                {
                    "Date": datetime.strptime(row["HistoricalDate"], "%d %b %Y").date(),
                    "Open": float(str(row["OPEN"]).replace(",", "")),
                    "High": float(str(row["HIGH"]).replace(",", "")),
                    "Low": float(str(row["LOW"]).replace(",", "")),
                    "Close": float(str(row["CLOSE"]).replace(",", "")),
                    "Volume": None,
                }
            )
        except (KeyError, ValueError):
            continue
    if not records:
        return None
    return pd.DataFrame(records).drop_duplicates("Date").set_index("Date").sort_index()


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


def fetch_nse_bhavcopy(day: date) -> pd.DataFrame | None:
    """All NSE cash-market equity OHLCV for a single day in one download (UDiFF bhavcopy).

    Returns Date/Symbol/Open/High/Low/Close/Volume (bare symbols) for the EQ/BE/BZ series, or
    None on a holiday/weekend/future date (no file) or a fetch failure.
    """
    from jugaad_data.nse import bhavcopy_raw  # noqa: PLC0415 — heavy optional dep, only on this path

    try:
        raw = bhavcopy_raw(day)
    except Exception as e:
        logger.debug("bhavcopy unavailable for %s: %s", day, e)
        return None
    try:
        df = pd.read_csv(StringIO(raw))
    except Exception:
        return None
    df.columns = [c.strip() for c in df.columns]
    if "Sgmt" not in df.columns or "TckrSymb" not in df.columns:
        return None
    eq = df[(df["Sgmt"] == "CM") & (df["SctySrs"].isin(["EQ", "BE", "BZ"]))]
    if eq.empty:
        return None
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(eq["TradDt"]).dt.date,
            "Symbol": eq["TckrSymb"].astype(str).str.strip(),
            "Open": eq["OpnPric"],
            "High": eq["HghPric"],
            "Low": eq["LwPric"],
            "Close": eq["ClsPric"],
            "Volume": eq["TtlTradgVol"],
        }
    )


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


def fetch_symbol_data_jugaad(symbol: str, start, end) -> pd.DataFrame | None:
    """
    Fetch historical data from NSE via jugaad-data.
    Symbol should be WITHOUT .NS suffix (e.g., 'RELIANCE' not 'RELIANCE.NS').
    """
    try:
        from jugaad_data.nse import stock_df  # noqa: PLC0415 — heavy optional dep; only on the NSE fallback path

        nse_symbol = symbol.replace(".NS", "").replace(".BO", "")
        df = stock_df(symbol=nse_symbol, from_date=start, to_date=end, series="EQ")
        if df.empty:
            return None

        # Rename columns to match yfinance format
        df = df.rename(
            columns={
                "DATE": "Date",
                "OPEN": "Open",
                "HIGH": "High",
                "LOW": "Low",
                "CLOSE": "Close",
                "VOLUME": "Volume",
            }
        )
        return df[["Date", "Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.debug(f"jugaad-data failed for {symbol}: {e}")
        return None


def get_symbol_info(symbol: str):
    """Fetch symbol info from yfinance."""
    ticker = yf.Ticker(symbol)
    return ticker.info
