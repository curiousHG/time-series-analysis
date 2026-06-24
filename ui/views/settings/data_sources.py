"""Settings → Data Sources reference table + chain-of-fetches expander."""

from __future__ import annotations

import streamlit as st

from ui.constants import DATA_SOURCES_TABLE, SCHEMA_TABLES


def render() -> None:
    st.caption("Where each kind of data comes from, what input is required to fetch it, and where it lands.")
    st.dataframe(DATA_SOURCES_TABLE, use_container_width=True, hide_index=True)

    st.markdown("**Schema map** — every MF table now FKs through `amfi_schemes.scheme_code`.")
    st.dataframe(SCHEMA_TABLES, use_container_width=True, hide_index=True)

    with st.expander("How fetches chain together"):
        st.markdown(
            """
**Adding a fund** (Screener → *Fetch for top N filtered funds*) calls
`services.registry_service.backfill_missing(scheme_names=...)`, which:

1. Resolves each name → `scheme_code` against `amfi_schemes`. If no exact match, mints a
   synthetic negative code (rare — 75 of them today, all funds genuinely missing from AMFI master).
2. Inserts `mf_registry` row with status `pending` for every source.
3. Fans out NAV (MFAPI) + metadata (AdvisorKhoj) in parallel — 8 workers, 50ms submit delay.
4. NAV save triggers `recompute_metrics` for the affected schemes → fills `mf_scheme_metrics`.
5. Per-source statuses (`available` / `unavailable`) flip on the `mf_registry` row as each fetch resolves.

Holdings are excluded from the default backfill (heavier scrape, separate ceiling); use
**Settings → Update All Holdings** when ready.

**Tradebook upload** writes raw rows to **mf_tradebook**. Resolution to scheme names happens
live in memory via `mf_tradebook.isin = amfi_schemes.isin_growth` — no `fund_mapping` table.

**Phase 1 + Phase 2 normalisation** dropped ~340 MB:

- `fund_house` and `category` text columns extracted into `mf_amc` and `mf_category` dim tables
  (15.8 MB freed on `amfi_schemes`).
- `scheme_name` dropped from `mf_nav` / `mf_metadata` / `mf_scheme_metrics` / `mf_registry`;
  every MF table FKs into `amfi_schemes.scheme_code` instead (336 MB freed on `mf_nav` alone).
- `scheme_code_map` dropped — every name→code lookup goes through `amfi_schemes` directly.

**Synthetic negative codes**: ~141 minted during the Phase 2 backfill for funds whose
`scheme_name` didn't exact-match `amfi_schemes` (mostly case drift: `Bharat 22 ETF` vs
`BHARAT 22 ETF`). Run `uv run python scripts/dedupe_synthetic_codes.py --apply` to merge
the case-mismatch ones (`LOWER(name) = LOWER(name)`) into their real AMFI codes; the
remaining ~75 are funds genuinely absent from AMFI master.

**The glue**: every external system reaches every other through
`amfi_schemes.scheme_code ⇄ scheme_name ⇄ isin_growth`.
            """
        )
