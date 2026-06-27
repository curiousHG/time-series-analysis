"""AgGrid table rendering for the MF Screener: project to display columns, wire header
filters / multi-row selection / copy, and return the AgGrid response for the page."""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

# Re-flow columns to fill the grid width on first render AND whenever the grid resizes (e.g.
# the browser window widens) — otherwise AG Grid leaves columns at fixed widths and a wider
# window just shows empty space on the right. sizeColumnsToFit respects each column's
# min/max width, so when many metrics are shown they stay readable and overflow to a
# horizontal scroll instead of being crushed.
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

    # Per-source tracking glyphs (✓ / ✗ / —) so the user can see which datasets are
    # missing per fund without leaving the screener.
    pdf["NAV"] = pdf["nav_status"].apply(status_cell) if "nav_status" in pdf.columns else "—"
    pdf["Holdings"] = pdf["holdings_status"].apply(status_cell) if "holdings_status" in pdf.columns else "—"
    pdf["Metadata"] = pdf["metadata_status"].apply(status_cell) if "metadata_status" in pdf.columns else "—"

    # DB-named columns → display names, ordered as in METRIC_RENAME so they don't shuffle.
    db_cols_in_order = [c for c in METRIC_RENAME if c in pdf.columns]
    extra_status = [c for c in ("NAV", "Holdings", "Metadata") if c in pdf.columns]
    pdf = pdf[db_cols_in_order + extra_status].rename(columns=METRIC_RENAME)

    # Visibility filter: identity always shown + user-selected metrics.
    visible = [c for c in IDENTITY_COLS if c in pdf.columns] + [c for c in visible_metrics if c in pdf.columns]
    pdf = pdf[visible]

    # Re-order to canonical left-to-right so columns don't shuffle as the user toggles.
    pdf = pdf[[c for c in DISPLAY_COL_ORDER if c in pdf.columns]]

    # Percent-scale stored decimal fractions for human-readable cells.
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
        floatingFilter=True,  # filter input directly under the column header
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

    # Multi-row selection (for the copy-to-TSV echo). `use_checkbox=False` keeps the
    # checkbox OFF the Scheme column — it gets its own dedicated column (injected after
    # build) so a click on the fund name *only* opens the fund and the checkbox *only*
    # toggles selection. One interactive affordance per column = no ambiguous targets.
    # `suppressRowClickSelection` ensures a name click never doubles as a row select.
    gob.configure_selection(
        selection_mode="multiple",
        use_checkbox=False,
        suppressRowClickSelection=True,
    )
    # Style the Scheme cell as a link (blue + pointer cursor) to advertise that
    # clicking it opens the fund in MF Analysis — see render_open_action.
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
        enableCellTextSelection=True,  # cells are text-selectable for native browser copy
        ensureDomOrder=True,  # Cmd/Ctrl+C honours visual order, not insertion order
        suppressCopyRowsToClipboard=False,
        copyHeadersToClipboard=True,  # include column names in the clipboard payload
        onGridSizeChanged=_FIT_COLUMNS,  # re-fit columns to width when the window/grid resizes
        onFirstDataRendered=_FIT_COLUMNS,  # ...and once on initial render
    )

    grid_options = gob.build()
    # Dedicated, field-less checkbox column for the copy-to-TSV selection, pinned hard left.
    # Kept separate from Scheme so the two click behaviours can't collide (see above).
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
        allow_unsafe_jscode=True,  # required for the sizeColumnsToFit resize callbacks above
        # `cellClicked` lets `render_open_action` open a fund straight from a single
        # click on its Scheme cell. The other three keep the selection-echo / filter /
        # sort sections in sync with the grid (they're the library defaults minus
        # `cellValueChanged`, which is irrelevant on this read-only grid).
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
        st.caption(
            "Tab-separated; paste straight into Excel / Sheets / Notion. The checkbox in "
            "the Scheme column drives the selection (Shift+click ranges, Cmd/Ctrl+click "
            "individual rows)."
        )
        st.code(sel_df.to_csv(sep="\t", index=False), language="text")


def _clicked_scheme(grid_response: dict) -> str | None:
    """Return the fund name when the user just clicked a Scheme cell, else None.

    Only acts on a Scheme-column click so clicks elsewhere (checkbox column, or a numeric
    cell being text-selected for copy) are left alone.
    """
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
            submit_delay=0.0,  # single fund, no inter-request rate limiting needed
        )
    # Clear the Analysis page's sidebar filters so the opened fund is guaranteed to
    # be in the selectbox options (leftover filters from a prior visit could exclude
    # it and make the selectbox raise on the pre-set value).
    st.session_state["mf_analysis_fund"] = scheme_name
    st.session_state["mf_analysis_search"] = ""
    for k in ("mf_analysis_amc", "mf_analysis_cat", "mf_analysis_plan", "mf_analysis_option"):
        st.session_state[k] = []
    st.session_state["mf_analysis_only_meta"] = False
    st.session_state["mf_analysis_only_holdings"] = False
    st.switch_page("ui/views/mutual_fund/page.py")
