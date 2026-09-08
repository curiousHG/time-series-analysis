"""Settings → Reference: the data-flow map as one mermaid diagram.

The diagram is generated from `data.sources.SOURCE_ROUTING` (sources) plus the node/edge tables
below, so the Reference page cannot drift from the routing registry. Boxes carry only the short
name; hovering a box shows what it returns, where it lands, and why that route was chosen.
"""

from __future__ import annotations

import html
import json
import re
from typing import TYPE_CHECKING

import streamlit as st
import streamlit.components.v1 as components

from data.sources import SOURCE_ROUTING

if TYPE_CHECKING:
    from collections.abc import Callable

_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"

_SOURCES: tuple[dict, ...] = (
    {"id": "AMFI", "label": "AMFI", "sub": "NAVAll.txt", "match": lambda s: s.startswith("AMFI")},
    {"id": "MFAPI", "label": "MFAPI", "sub": "api.mfapi.in", "match": lambda s: s.startswith("MFAPI")},
    {"id": "AK", "label": "AdvisorKhoj", "sub": "scraped", "match": lambda s: s.startswith("AdvisorKhoj")},
    {"id": "KUV", "label": "Kuvera", "sub": "fallback", "match": lambda s: s.startswith("Kuvera")},
    {"id": "KITE", "label": "Zerodha CSV", "sub": "upload", "match": lambda s: s.startswith("Kite")},
    {"id": "YF", "label": "yfinance", "sub": "Yahoo", "match": lambda s: s.startswith("yfinance")},
    {"id": "BHAV", "label": "NSE index bhavcopy", "sub": "daily file", "match": lambda s: "index bhavcopy" in s},
    {
        "id": "NSE",
        "label": "NSE lists",
        "sub": "EQUITY_L · ETF · constituents",
        "match": lambda s: s.startswith("NSE ") and "bhavcopy" not in s,
    },
    {"id": "SCR", "label": "screener.in", "sub": "scraped", "match": lambda s: s.startswith("screener.in")},
)

_TABLES: tuple[dict, ...] = (
    {
        "id": "SCHEMES",
        "label": "amfi_schemes",
        "key": "scheme_code",
        "holds": "Every AMFI scheme (~14.6K) with ISINs, plan/option, latest NAV, AMC and category dims. "
        "Synthetic negative codes stand in for tracked funds AMFI does not list.",
        "note": "Every mf_* table FKs through this code; tradebook rows join on isin_growth.",
    },
    {
        "id": "REG",
        "label": "mf_registry",
        "key": "scheme_code",
        "holds": "The tracked-fund list with per-source status (nav / holdings / metadata) and the verified "
        "AdvisorKhoj slug.",
        "note": "Dormant schemes (AMFI NAV older than 90 days, or NAV ≤ 0 side pockets) are skipped by every refresh.",
    },
    {
        "id": "NAV",
        "label": "mf_nav",
        "key": "(scheme_code, date)",
        "holds": "Daily NAV history per tracked scheme (~7.8M rows).",
        "note": "Incremental saves keep only date > last stored; single-day glitches and 10x scale breaks are repaired.",
    },
    {
        "id": "HOLD",
        "label": "mf_holdings",
        "key": "id · scheme_code FK",
        "holds": "Instrument-level holdings with weights and ISINs, plus mf_sector_allocation and "
        "mf_asset_allocation for the same portfolio date.",
        "note": "Replaced atomically per fund. Arbitrage funds legitimately sum ≠ 100% (short legs).",
    },
    {
        "id": "META",
        "label": "mf_metadata",
        "key": "scheme_code",
        "holds": "AUM, TER, benchmark, fund manager, exit load, launch date, riskometer.",
        "note": "Regular plans borrow scheme-level fields (AUM, manager, riskometer) from their Direct sibling; "
        "TER and minimums are never copied across plans.",
    },
    {
        "id": "MFMET",
        "label": "mf_scheme_metrics",
        "key": "scheme_code",
        "holds": "31 cached risk/return metrics per scheme: CAGR, Sharpe, VaR, rolling consistency, "
        "alpha/beta vs the category benchmark.",
        "note": "Recomputed after every NAV save so the screener never scans NAV rows. Segregated and wound-up "
        "schemes get no row.",
    },
    {
        "id": "TRADES",
        "label": "mf_tradebook",
        "key": "trade_id",
        "holds": "Kite/Zerodha trade rows (ISIN, qty, price, date).",
        "note": "Deduped by trade_id on import; ISIN resolves to a scheme via amfi_schemes at read time.",
    },
    {
        "id": "SREG",
        "label": "stock_registry",
        "key": "symbol",
        "holds": "Every pickable equity/ETF/index with exchange, ISIN, quote type and OHLCV/fundamentals watermarks.",
        "note": "exchange = NULL marks a retired symbol (history kept, refresh skipped).",
    },
    {
        "id": "OHLCV",
        "label": "stock_ohlcv",
        "key": "(date, symbol)",
        "holds": "Daily adjusted OHLCV for ~510 NSE symbols plus international tickers.",
        "note": "One price basis only (yfinance auto-adjusted). Refresh anchors on the NSE median date; phantom "
        "weekend bars are rejected outside real special sessions.",
    },
    {
        "id": "IDX",
        "label": "index_ohlcv",
        "key": "(date, symbol)",
        "holds": "Levels for 160+ NSE indices (bhavcopy names) with P/E, P/B and dividend yield, plus "
        "^-prefixed foreign indices and FX.",
        "note": "Index levels are unadjusted by nature; implausible one-day moves are rejected at the write path.",
    },
    {
        "id": "SMET",
        "label": "stock_metrics",
        "key": "symbol",
        "holds": "screener.in ratios and shareholding, quarterly P&L in stock_quarterly, and CAPM alpha/beta/R² "
        "over the trailing year.",
        "note": "Benchmark follows the listing: ^NSEI for NSE, ^GSPC for international.",
    },
)

_PAGES: tuple[dict, ...] = (
    {
        "id": "P_OV",
        "label": "Overview",
        "reads": "Market pulse from index levels, portfolio snapshot from trades + NAV, alerts and fund movers from cached metrics.",
    },
    {
        "id": "P_PF",
        "label": "Portfolio",
        "reads": "Positions and XIRR from trades + NAV; look-through exposure and overlap from holdings; benchmark from index levels.",
    },
    {
        "id": "P_MF",
        "label": "MF Analysis",
        "reads": "NAV history, cached metrics, metadata and holdings for one fund; category benchmark from index levels.",
    },
    {
        "id": "P_MFS",
        "label": "MF Screener",
        "reads": "AMFI universe joined to cached metrics and metadata; bulk fetch adds funds to the registry.",
    },
    {
        "id": "P_SA",
        "label": "Stock Analysis",
        "reads": "Candles from stock/index OHLCV, fundamentals from stock_metrics, picker from stock_registry.",
    },
    {
        "id": "P_SS",
        "label": "Stock Screener",
        "reads": "stock_metrics only: fundamentals plus alpha/beta categorisation.",
    },
)

_EDGES: tuple[tuple[str, str, str, str], ...] = (
    ("AMFI", "SCHEMES", "", "solid"),
    ("MFAPI", "NAV", "", "solid"),
    ("AK", "HOLD", "", "solid"),
    ("AK", "META", "", "solid"),
    ("KUV", "META", "fallback", "dotted"),
    ("KITE", "TRADES", "", "solid"),
    ("YF", "OHLCV", "", "solid"),
    ("YF", "IDX", "foreign · FX", "solid"),
    ("BHAV", "IDX", "", "solid"),
    ("NSE", "SREG", "", "solid"),
    ("YF", "SREG", "add intl", "dotted"),
    ("SCR", "SMET", "", "solid"),
    ("SCHEMES", "REG", "tracked", "solid"),
    ("NAV", "MFMET", "recompute", "solid"),
    ("OHLCV", "SMET", "CAPM", "solid"),
    ("IDX", "SMET", "benchmark", "solid"),
    ("NAV", "P_OV", "", "solid"),
    ("IDX", "P_OV", "", "solid"),
    ("MFMET", "P_OV", "", "solid"),
    ("TRADES", "P_PF", "", "solid"),
    ("NAV", "P_PF", "", "solid"),
    ("HOLD", "P_PF", "", "solid"),
    ("NAV", "P_MF", "", "solid"),
    ("HOLD", "P_MF", "", "solid"),
    ("META", "P_MF", "", "solid"),
    ("SCHEMES", "P_MFS", "", "solid"),
    ("MFMET", "P_MFS", "", "solid"),
    ("SREG", "P_SA", "", "solid"),
    ("OHLCV", "P_SA", "", "solid"),
    ("SMET", "P_SA", "", "solid"),
    ("SMET", "P_SS", "", "solid"),
)

_ENSURE_FLOW = """
flowchart LR
    A["page asks for<br/>keys + range"] --> B["ensure_*()"]
    B --> C{"already<br/>in DB?"}
    C -- "missing gap" --> D["fetcher (HTTP)"]
    D --> E["save · ON CONFLICT<br/>DO UPDATE"]
    E --> F["return rows<br/>from DB"]
    C -- "cached" --> F
    C -- "marked<br/>unavailable" --> G["skip fetch"]
    classDef step fill:#1e293b,stroke:#475569,color:#e2e8f0;
    classDef hot fill:#312e81,stroke:#6366f1,color:#e0e7ff;
    class A,C,E,F,G step;
    class B,D hot;
"""


def node_ids() -> list[str]:
    return [n["id"] for n in (*_SOURCES, *_TABLES, *_PAGES)]


def is_external_row(row: dict) -> bool:
    """Derived rows (services.*) and the benchmark-routing rule (^NSEI / ^GSPC) are DB-internal."""
    return not row["source"].startswith(("services.", "^"))


def routing_rows_for(node: dict) -> list[dict]:
    match: Callable[[str], bool] = node["match"]
    return [r for r in SOURCE_ROUTING if match(r["source"]) or match(r["fallback"])]


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _source_tip(node: dict) -> str:
    rows = routing_rows_for(node)
    items = []
    for r in rows:
        role = "fallback for" if node["match"](r["fallback"]) and not node["match"](r["source"]) else ""
        items.append(
            f"<li><b>{_esc(r['what'])}</b>{' <i>(' + role + ')</i>' if role else ''}"
            f"<div class='tip-line'>→ <code>{_esc(r['lands_in'])}</code></div>"
            f"<div class='tip-line'>{_esc(r['returns'])}</div>"
            f"<div class='tip-why'>{_esc(r['why'])}</div></li>"
        )
    return (
        f"<div class='tip-title'>{_esc(node['label'])} <span>{_esc(node['sub'])}</span></div><ul>{''.join(items)}</ul>"
    )


def _table_tip(node: dict) -> str:
    return (
        f"<div class='tip-title'>{_esc(node['label'])} <span>key {_esc(node['key'])}</span></div>"
        f"<div class='tip-line'>{_esc(node['holds'])}</div>"
        f"<div class='tip-why'>{_esc(node['note'])}</div>"
    )


def _page_tip(node: dict) -> str:
    return f"<div class='tip-title'>{_esc(node['label'])} <span>page</span></div><div class='tip-line'>{_esc(node['reads'])}</div>"


def tooltips() -> dict[str, str]:
    tips = {n["id"]: _source_tip(n) for n in _SOURCES}
    tips |= {n["id"]: _table_tip(n) for n in _TABLES}
    tips |= {n["id"]: _page_tip(n) for n in _PAGES}
    return tips


def build_flow_diagram() -> str:
    lines = ["flowchart LR"]
    lines.append('    subgraph SRC["Sources"]')
    lines.append("        direction TB")
    lines.extend(f'        {n["id"]}["{_esc(n["label"])}<br/><small>{_esc(n["sub"])}</small>"]' for n in _SOURCES)
    lines.append("    end")
    lines.append('    subgraph DB["PostgreSQL"]')
    lines.append("        direction TB")
    lines.extend(f'        {n["id"]}[("{_esc(n["label"])}")]' for n in _TABLES)
    lines.append("    end")
    lines.append('    subgraph PAGES["Pages"]')
    lines.append("        direction TB")
    lines.extend(f'        {n["id"]}(["{_esc(n["label"])}"])' for n in _PAGES)
    lines.append("    end")
    for src, dst, label, style in _EDGES:
        arrow = "-.->" if style == "dotted" else "-->"
        text = f'|"{_esc(label)}"|' if label else ""
        lines.append(f"    {src} {arrow}{text} {dst}")
    lines += [
        "    classDef source fill:#1e1b4b,stroke:#6366f1,color:#e0e7ff,stroke-width:1.5px;",
        "    classDef table fill:#1e293b,stroke:#64748b,color:#e2e8f0;",
        "    classDef page fill:#052e16,stroke:#10b981,color:#d1fae5,stroke-width:1.5px;",
        f"    class {','.join(n['id'] for n in _SOURCES)} source;",
        f"    class {','.join(n['id'] for n in _TABLES)} table;",
        f"    class {','.join(n['id'] for n in _PAGES)} page;",
        "    style SRC fill:transparent,stroke:#312e81,stroke-dasharray:4 3,color:#a5b4fc;",
        "    style DB fill:transparent,stroke:#334155,stroke-dasharray:4 3,color:#94a3b8;",
        "    style PAGES fill:transparent,stroke:#064e3b,stroke-dasharray:4 3,color:#6ee7b7;",
    ]
    return "\n".join(lines)


def validate_diagram() -> list[str]:
    """Structural checks used by the test-suite: unique ids, edges resolve, routing rows covered once."""
    problems: list[str] = []
    ids = node_ids()
    if len(ids) != len(set(ids)):
        problems.append("duplicate node ids")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", i) for i in ids):
        problems.append("node id is not a mermaid-safe identifier")
    for a, b, _, _ in _EDGES:
        if a not in ids or b not in ids:
            problems.append(f"edge {a}->{b} references an unknown node")
    for row in SOURCE_ROUTING:
        if not is_external_row(row):
            continue
        owners = [n["id"] for n in _SOURCES if n["match"](row["source"])]
        if len(owners) != 1:
            problems.append(f"routing row {row['what']!r} matched {owners or 'no'} source nodes")
    return problems


_TIP_CSS = """
  body { margin: 0; background: transparent; font-family: Inter, system-ui, sans-serif; }
  .mermaid { text-align: center; }
  .mermaid svg { max-width: 100%; }
  .mermaid .node, .mermaid .node * { cursor: help; }
  .mermaid .node.hover rect, .mermaid .node.hover path, .mermaid .node.hover polygon {
    filter: drop-shadow(0 0 6px #818cf8); }
  .mermaid .cluster-label, .mermaid .cluster-label * { font-weight: 600; letter-spacing: .04em; }
  .mermaid .edgeLabel { background: #0f1117; color: #94a3b8; font-size: 11px; }
  .mermaid .node small { color: #a5b4fc; font-size: 10px; }
  #tip { position: fixed; z-index: 10; max-width: 380px; padding: 10px 12px; border-radius: 8px;
    background: #111827; color: #e2e8f0; border: 1px solid #334155; box-shadow: 0 8px 24px rgba(0,0,0,.5);
    font-size: 12px; line-height: 1.45; pointer-events: none; opacity: 0; transition: opacity .12s; }
  #tip ul { margin: 6px 0 0; padding-left: 16px; }
  #tip li + li { margin-top: 8px; }
  #tip code { color: #6ee7b7; font-size: 11px; }
  #tip .tip-title { font-weight: 600; font-size: 13px; color: #f1f5f9; }
  #tip .tip-title span { font-weight: 400; color: #94a3b8; font-size: 11px; margin-left: 6px; }
  #tip .tip-line { color: #cbd5e1; margin-top: 3px; }
  #tip .tip-why { color: #94a3b8; font-style: italic; margin-top: 3px; }
"""

_HOVER_JS = """
  const tip = document.getElementById('tip');
  const idOf = el => (el.id.match(/^flowchart-([A-Za-z0-9_]+)-\\d+$/) || [])[1];
  const place = e => {
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = Math.max(8, window.innerHeight - h - 8);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  };
  document.querySelectorAll('.mermaid g.node').forEach(el => {
    const html = TIPS[idOf(el)];
    if (!html) return;
    el.addEventListener('mouseenter', e => { tip.innerHTML = html; tip.style.opacity = 1; el.classList.add('hover'); place(e); });
    el.addEventListener('mousemove', place);
    el.addEventListener('mouseleave', () => { tip.style.opacity = 0; el.classList.remove('hover'); });
  });
"""


def _mermaid(code: str, *, height: int, tips: dict[str, str] | None = None) -> None:
    tips_json = json.dumps(tips or {})
    components.html(
        f"""
        <style>{_TIP_CSS}</style>
        <div class="mermaid">{code}</div>
        <div id="tip"></div>
        <script type="module">
          import mermaid from '{_MERMAID_CDN}';
          mermaid.initialize({{ startOnLoad: false, theme: 'dark', securityLevel: 'loose',
                                flowchart: {{ curve: 'basis', nodeSpacing: 22, rankSpacing: 70, padding: 10 }},
                                themeVariables: {{ fontSize: '13px', fontFamily: 'Inter, system-ui, sans-serif',
                                                   lineColor: '#475569', edgeLabelBackground: '#0f1117' }} }});
          await mermaid.run();
          const TIPS = {tips_json};
          {_HOVER_JS}
        </script>
        """,
        height=height,
        scrolling=True,
    )


def render() -> None:
    st.caption(
        "Sources on the left, PostgreSQL in the middle, pages on the right. Hover any box for what it returns, where it lands, and why that route was chosen."
    )
    _mermaid(build_flow_diagram(), height=880, tips=tooltips())
    st.caption(
        "Dotted arrow = secondary route (fallback or on-demand). Every `mf_*` table joins through "
        "`amfi_schemes.scheme_code`; stocks and indices are keyed by bare symbol or NSE index name. "
        "Portfolio and MF Analysis also read `index_ohlcv` for benchmark comparisons (arrows omitted to keep the map readable)."
    )
    with st.expander("How a fetch works (DB-first rule)"):
        st.caption(
            "Check the DB, fetch only the missing gap, save it back, return from the DB. Every page goes through `ensure_*`, never a fetcher."
        )
        _mermaid(_ENSURE_FLOW, height=210)
