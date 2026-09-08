"""Market-data queries for the UI — thin service seam over data/ so views and cache loaders don't
reach into repositories/fetchers directly (the ui → services → data contract). Callable by a bot/CLI
too, and testable without Streamlit."""

from __future__ import annotations

import re

from data.fetchers.screener_in import fetch_index_constituents

# NSE index name → screener.in slug (its /company/<slug>/ page lists constituents). Sectoral/broad
# ones are known; factor indices fall back to a squashed-uppercase heuristic (best-effort).
_INDEX_NAME_SLUG = {
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
    """The unified Stock-Analysis picker universe: every equity + ETF (from stock_registry) and every
    index (NSE bhavcopy names + international), each tagged with its kind so the UI can badge it and
    route to the right view. Entry: {id, kind: 'stock'|'etf'|'index', name}."""
    from data.repositories.stock import list_bhavcopy_index_names, load_registry_catalog  # noqa: PLC0415
    from services.insights_service import INTERNATIONAL_INDEX_SYMBOLS  # noqa: PLC0415

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
    """Live NSE ETF metadata keyed by symbol (underlying, NAV, last price, 52-week range, 30d/1Y
    performance) — one call to the NSE ETF API. Empty dict on failure."""
    from data.fetchers.stock import fetch_nse_etf_list  # noqa: PLC0415 — defer heavy import off boot

    return {e["symbol"]: e for e in fetch_nse_etf_list()}


def index_constituents(index_name: str) -> list[str]:
    """Constituent NSE symbols of an index. Prefers NSE's official `ind_<name>list.csv` (full lists
    for broad/sectoral indices), then falls back to screener.in (sectoral + Nifty 50). Returns [] for
    indices neither source lists (many factor/strategy indices)."""
    from data.fetchers.stock import fetch_nse_index_constituents  # noqa: PLC0415 — defer off boot

    nse = fetch_nse_index_constituents(index_name)
    if nse:
        return nse
    slug = _INDEX_NAME_SLUG.get(index_name.strip().lower()) or re.sub(r"[^A-Za-z0-9]", "", index_name).upper()
    return fetch_index_constituents(slug)
