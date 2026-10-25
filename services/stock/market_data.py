"""Market-data queries for the UI: a thin service seam over data/ for views and cache loaders."""

from __future__ import annotations

import re

from data.fetchers.screener_in import fetch_index_constituents

_SCREENER_SLUG_BY_INDEX_NAME = {
    "nifty 50": "NIFTY",
    "nifty bank": "BANKNIFTY",
    "nifty next 50": "NIFTYNEXT50",
    "nifty it": "CNXIT",
    "nifty pharma": "CNXPHARMA",
    "nifty auto": "CNXAUTO",
    "nifty fmcg": "CNXFMCG",
    "nifty metal": "CNXMETAL",
    "nifty energy": "CNXENERGY",
    "nifty realty": "CNXREALTY",
    "nifty infrastructure": "CNXINFRA",
    "nifty psu bank": "CNXPSUBANK",
    "nifty midcap 150": "NIFTYMIDCAP150",
    "nifty smallcap 250": "NIFTYSMLCAP250",
}


def analysis_catalog() -> list[dict]:
    """The Stock-Analysis picker universe: equities, ETFs and indices as {id, kind: 'stock'|'etf'|'index', name,
    exchange}."""
    from data.repositories.stock import list_bhavcopy_index_names, load_registry_catalog  # noqa: PLC0415
    from services.overview.market import INTERNATIONAL_INDEX_SYMBOLS  # noqa: PLC0415

    entries: list[dict] = [
        {
            "id": row["symbol"],
            "kind": "etf" if row["quote_type"] == "ETF" else "stock",
            "name": row["name"] or "",
            "exchange": row["exchange"],
        }
        for row in load_registry_catalog().iter_rows(named=True)
    ]
    entries.extend(
        {"id": name, "kind": "index", "name": name, "exchange": "NSE"} for name in list_bhavcopy_index_names()
    )
    entries.extend(
        {"id": sym, "kind": "index", "name": disp, "exchange": None}
        for sym, disp, _region in INTERNATIONAL_INDEX_SYMBOLS
    )
    return entries


def global_ticker_search(query: str) -> list[dict]:
    """Yahoo-wide ticker search (equities/ETFs on any exchange) for the add-international flow."""
    from data.fetchers.stock import search_global_tickers  # noqa: PLC0415 — defer heavy import off boot

    return search_global_tickers(query)


def global_fundamentals(symbol: str) -> dict | None:
    """yfinance fundamentals (+ quarterly income) for an international ticker."""
    from data.fetchers.stock import fetch_global_fundamentals  # noqa: PLC0415 — defer off boot

    return fetch_global_fundamentals(symbol)


def etf_metadata() -> dict[str, dict]:
    """Live NSE ETF metadata keyed by symbol, from one NSE ETF API call. Empty dict on failure."""
    from data.fetchers.stock import fetch_nse_etf_list  # noqa: PLC0415 — defer heavy import off boot

    return {e["symbol"]: e for e in fetch_nse_etf_list()}


def index_constituents(index_name: str) -> list[str]:
    """Constituent NSE symbols of an index: NSE's official list, else screener.in. [] when neither lists it."""
    from data.fetchers.stock import fetch_nse_index_constituents  # noqa: PLC0415 — defer off boot

    nse = fetch_nse_index_constituents(index_name)
    if nse:
        return nse
    slug = (
        _SCREENER_SLUG_BY_INDEX_NAME.get(index_name.strip().lower()) or re.sub(r"[^A-Za-z0-9]", "", index_name).upper()
    )
    return fetch_index_constituents(slug)
