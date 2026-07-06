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
