"""AgGrid table rendering for the MF Screener: project to display columns, wire header
filters / multi-row selection / copy, and return the AgGrid response for the page."""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

# Re-fit columns to grid width on first render and on resize. Respects each column's
# min/max, so many metrics overflow to a horizontal scroll instead of being crushed.
_FIT_COLUMNS = JsCode("function(params) { params.api.sizeColumnsToFit(); }")

from mutual_funds.metric_catalog import (
    DISPLAY_COL_ORDER,
    IDENTITY_COLS,
    METRIC_NUMERIC_COLS,
    METRIC_PCT_COLS,
    METRIC_RENAME,
    METRIC_TEXT_COLS,
)
from services.registry_service import backfill_missing
from services.screener_service import status_cell


def _build_display_pdf(filtered: pl.DataFrame, visible_metrics: list[str]) -> pd.DataFrame:
    """Project + rename + percent-scale the screener frame to match the AgGrid display."""
    pdf = filtered.to_pandas()

    # Per-source tracking glyphs (✓ / ✗ / —) for which datasets each fund has.
    pdf["NAV"] = pdf["nav_status"].apply(status_cell) if "nav_status" in pdf.columns else "—"
    pdf["Holdings"] = pdf["holdings_status"].apply(status_cell) if "holdings_status" in pdf.columns else "—"
    pdf["Metadata"] = pdf["metadata_status"].apply(status_cell) if "metadata_status" in pdf.columns else "—"

    # DB names → display names, ordered as in METRIC_RENAME.
    db_cols_in_order = [c for c in METRIC_RENAME if c in pdf.columns]
    extra_status = [c for c in ("NAV", "Holdings", "Metadata") if c in pdf.columns]
    pdf = pdf[db_cols_in_order + extra_status].rename(columns=METRIC_RENAME)

    # Visibility filter: identity always shown + user-selected metrics.
    visible = [c for c in IDENTITY_COLS if c in pdf.columns] + [c for c in visible_metrics if c in pdf.columns]
    pdf = pdf[visible]

    # Canonical left-to-right order so columns don't shuffle as the user toggles.
    pdf = pdf[[c for c in DISPLAY_COL_ORDER if c in pdf.columns]]

    # Percent-scale stored decimal fractions.
    for col in METRIC_PCT_COLS:
        if col in pdf.columns:
            pdf[col] = pdf[col] * 100
    return pdf


def _build_grid_options(pdf: pd.DataFrame):
    """Configure AgGrid: header filters, numeric vs text filter type, pinned Scheme,
    multi-row selection, native Cmd/Ctrl+C copy.
    """
    gob = GridOptionsBuilder.from_dataframe(pdf)
    gob.configure_default_column(
        sortable=True,
        resizable=True,
        filter=True,
        floatingFilter=True,  # filter input under the column header
        minWidth=80,
    )
    for c in pdf.columns:
        if c in METRIC_NUMERIC_COLS:
            gob.configure_column(
                c,
                type=["numericColumn"],
                filter="agNumberColumnFilter",
                valueFormatter="x == null ? '' : Number(x).toFixed(2)",
            )
        elif c in METRIC_TEXT_COLS:
            gob.configure_column(c, filter="agTextColumnFilter")

    # Multi-row selection for the copy-to-TSV echo. The checkbox lives in its own column
    # (injected after build), so a Scheme click only opens the fund and never selects a row.
    gob.configure_selection(
        selection_mode="multiple",
        use_checkbox=False,
        suppressRowClickSelection=True,
    )
    # Style the Scheme cell as a link — clicking it opens the fund (see render_open_action).
    gob.configure_column(
        "Scheme",
        pinned="left",
        minWidth=300,
        cellStyle={"color": "#5aa9ff", "cursor": "pointer", "fontWeight": 600},
    )

    gob.configure_grid_options(
        domLayout="normal",
        suppressHorizontalScroll=False,
        alwaysShowVerticalScroll=True,
        enableCellTextSelection=True,  # text-selectable cells for native copy
        ensureDomOrder=True,  # Cmd/Ctrl+C honours visual order
        suppressCopyRowsToClipboard=False,
        copyHeadersToClipboard=True,  # include column names in the clipboard
        onGridSizeChanged=_FIT_COLUMNS,  # re-fit on resize
        onFirstDataRendered=_FIT_COLUMNS,  # ...and on initial render
    )

    grid_options = gob.build()
    # Field-less checkbox column for selection, pinned hard left and kept off Scheme.
    grid_options["columnDefs"].insert(
        0,
        {
            "headerName": "",
            "colId": "_select",
            "checkboxSelection": True,
            "headerCheckboxSelection": True,
            "headerCheckboxSelectionFilteredOnly": True,
            "pinned": "left",
            "width": 44,
            "minWidth": 44,
            "maxWidth": 44,
            "filter": False,
            "floatingFilter": False,
            "sortable": False,
            "resizable": False,
            "suppressMovable": True,
            "lockPosition": True,
        },
    )
    return grid_options


def render_table(
    filtered: pl.DataFrame,
    visible_metrics: list[str],
    aggrid_theme: Any,
) -> tuple[pd.DataFrame, dict]:
    """Build + render the AgGrid table. Returns the display DataFrame (reusable without
    rebuilding) and the AgGrid response (for selection echo)."""
    display_pdf = _build_display_pdf(filtered, visible_metrics)
    grid_options = _build_grid_options(display_pdf)
    grid_response = AgGrid(
        display_pdf,
        gridOptions=grid_options,
        height=650,
        theme=aggrid_theme,
        allow_unsafe_jscode=True,  # required for the sizeColumnsToFit callbacks
        # `cellClicked` drives open-on-click; the rest keep selection / filter / sort in sync.
        update_on=["cellClicked", "selectionChanged", "filterChanged", "sortChanged"],
    )
    return display_pdf, grid_response


def _selected_rows_df(grid_response: dict) -> pd.DataFrame | None:
    """Normalise AgGrid's selected_rows (list-of-dicts or DataFrame) to a DataFrame."""
    selected = grid_response.get("selected_rows")
    if selected is None or len(selected) == 0:
        return None
    return pd.DataFrame(selected) if isinstance(selected, list) else selected


def render_selection_echo(grid_response: dict) -> None:
    """Render an expander with the selected rows as TSV — paste-ready for Excel / Sheets."""
    sel_df = _selected_rows_df(grid_response)
    if sel_df is None:
        return
    with st.expander(f"📋 {len(sel_df)} selected row(s) — copy", expanded=False):
        st.caption("Tab-separated; paste straight into Excel / Sheets / Notion.")
        st.code(sel_df.to_csv(sep="\t", index=False), language="text")


def _clicked_scheme(grid_response: dict) -> str | None:
    """Return the fund name when the user just clicked a Scheme cell, else None."""
    event = grid_response.get("event_data") or {}
    if event.get("streamlitRerunEventTriggerName") != "cellClicked":
        return None
    if (event.get("colDef") or {}).get("field") != "Scheme":
        return None
    scheme_name = (event.get("data") or {}).get("Scheme") or event.get("value")
    return str(scheme_name) if scheme_name else None


def render_open_action(grid_response: dict) -> None:
    """Open a fund in MF Analysis when its Scheme cell is clicked: register it, pull NAV +
    metadata, pre-select, switch pages. Holdings deferred to the Analysis page on load."""
    scheme_name = _clicked_scheme(grid_response)
    if scheme_name is None:
        return

    with st.spinner(f"Fetching NAV + metadata for {scheme_name}…"):
        backfill_missing(
            scheme_names=[scheme_name],
            sources=("nav", "metadata"),
            max_per_run=2,
            submit_delay=0.0,  # single fund, no rate limiting needed
        )
    # Clear the Analysis page's filters so the opened fund stays in the selectbox options.
    st.session_state["mf_analysis_fund"] = scheme_name
    st.session_state["mf_analysis_search"] = ""
    for k in ("mf_analysis_amc", "mf_analysis_cat", "mf_analysis_plan", "mf_analysis_option"):
        st.session_state[k] = []
    st.session_state["mf_analysis_only_meta"] = False
    st.session_state["mf_analysis_only_holdings"] = False
    st.switch_page("ui/views/mutual_fund/page.py")
