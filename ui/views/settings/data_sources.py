"""Settings → Reference: data-source & schema tables + mermaid architecture/data-flow diagrams.

Diagrams are rendered with mermaid.js (via components.html) and reflect the graphify knowledge graph
(graphify-out/GRAPH_REPORT.md): the ui → services → data layering and the DB-first "ensure" pattern."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from ui.constants import DATA_SOURCES_TABLE, SCHEMA_TABLES

# ---- Architecture (ui → services → domain → repositories/fetchers → PostgreSQL). god-node degrees
# (edges) from graphify's God-Nodes list are called out so the core abstractions stand out.
_ARCHITECTURE = """
flowchart TB
    EXT["External sources<br/>AMFI · MFAPI · AdvisorKhoj · Kuvera<br/>screener.in · yfinance · NSE bhavcopy"]
    subgraph UI["ui/ — Streamlit (rendering only)"]
        PAGES["pages · charts · components<br/>render() ·91· · Kpi ·28· · BackgroundRefresh ·20·"]
        LOAD["ui/state/loaders.py<br/>@st.cache_data wrappers"]
    end
    subgraph SVC["services/ — business logic (no Streamlit)"]
        S["build_alerts() ·24· · recompute_metrics() ·19·<br/>market_data_service · sync_service · insights"]
    end
    DOM["core/ · indicators/ · strategies/ · mutual_funds/ · stocks/"]
    subgraph DATA["data/"]
        REPO["repositories/ — DB CRUD (no HTTP)<br/>ensure_stock_data() ·19· · ensure_nav_data() · get_session() ·91·"]
        FET["fetchers/ — HTTP only (no DB)"]
    end
    DB[("PostgreSQL<br/>18 tables")]

    PAGES --> LOAD --> SVC
    SVC --> DOM
    SVC --> REPO
    REPO --> DB
    REPO --> FET --> EXT
"""

# ---- DB-first "ensure_*" pattern: the project's hard rule — fetch only the gap, then read from DB.
_ENSURE_FLOW = """
flowchart LR
    A["UI asks for<br/>data (keys, range)"] --> B["ensure_*()"]
    B --> C{"already<br/>in DB?"}
    C -- "missing gap" --> D["fetcher (HTTP)"]
    D --> E["save to PostgreSQL<br/>ON CONFLICT DO UPDATE"]
    E --> F["return rows from DB"]
    C -- "cached" --> F
    C -- "watermark:<br/>unavailable" --> G["short-circuit<br/>(no fetch)"]
"""

# ---- The two bulk paths that keep the whole universe fresh from single downloads.
_BULK_FLOW = """
flowchart LR
    subgraph Funds
        BN["backfill_missing(names)"] --> R["mf_registry (pending)"]
        R --> NAV["MFAPI NAV + AdvisorKhoj meta<br/>(8 workers, parallel)"]
        NAV --> M["recompute_metrics ·19·"] --> MM[("mf_scheme_metrics")]
    end
    subgraph Bhavcopy
        DAY["one trading day"] --> SB["save_bhavcopy_day"] --> SO[("stock_ohlcv")]
        DAY --> IB["save_index_bhavcopy_day<br/>160+ indices + P/E·P/B·DivYield"] --> IO[("index_ohlcv")]
    end
"""


def _mermaid(code: str, *, height: int) -> None:
    """Render a mermaid diagram via components.html (mermaid.js from CDN, theme-matched to the app)."""
    components.html(
        f"""
        <div class="mermaid" style="text-align:center">{code}</div>
        <script type="module">
          import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
          mermaid.initialize({{ startOnLoad: false, theme: 'dark', securityLevel: 'loose',
                                themeVariables: {{ fontSize: '13px' }} }});
          await mermaid.run();
        </script>
        """,
        height=height,
        scrolling=True,
    )


def render() -> None:
    st.caption("Where each kind of data comes from, what input is required to fetch it, and where it lands.")
    st.dataframe(DATA_SOURCES_TABLE, use_container_width=True, hide_index=True)

    st.markdown("**Schema map** — every MF table FKs through `amfi_schemes.scheme_code`; the stock/index side is bare-keyed.")
    st.dataframe(SCHEMA_TABLES, use_container_width=True, hide_index=True)
    st.caption(
        "Dropped in the July 2026 cleanup: `stock_ohlcv_status`, `scheme_code_map`, `bots`, `orders`, `trades` "
        "(unused/superseded — OHLCV status now lives on `stock_registry.ohlcv_*`)."
    )

    st.divider()
    st.markdown("#### Architecture")
    st.caption(
        "The `ui → services → data` layering (from the graphify knowledge graph). Numbers like `·91·` are a node's "
        "edge count in the graph — the higher, the more central the abstraction. `get_session()` (91) and "
        "`ensure_stock_data()` (19) are the data-layer hubs; `build_alerts()`/`recompute_metrics()` the service hubs."
    )
    _mermaid(_ARCHITECTURE, height=460)

    st.markdown("#### DB-first `ensure_*` pattern")
    st.caption("The project's hard rule: check the DB, fetch only the missing gap, save it back, return from the DB.")
    _mermaid(_ENSURE_FLOW, height=230)

    st.markdown("#### Bulk refresh paths")
    st.caption("How a few downloads keep the whole universe fresh — parallel fund backfill + one-file-per-day bhavcopy.")
    _mermaid(_BULK_FLOW, height=340)

    st.caption(
        "The glue: every MF system reaches every other through `amfi_schemes.scheme_code ⇄ scheme_name ⇄ isin_growth`; "
        "stocks/indices are keyed by bare symbol / NSE index name."
    )
