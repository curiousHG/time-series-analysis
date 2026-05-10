"""AgGrid table rendering for the MF Screener.

Takes the sidebar-filtered polars DataFrame, projects to display columns (using the
catalog's rename map + canonical order), wires AgGrid's per-column header filters,
multi-row selection, and copy-to-clipboard, and returns the AgGrid response so the
page orchestrator can handle the row-selection echo separately.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder

from mutual_funds.metric_catalog import (
    DISPLAY_COL_ORDER,
    IDENTITY_COLS,
    METRIC_NUMERIC_COLS,
    METRIC_PCT_COLS,
    METRIC_RENAME,
    METRIC_TEXT_COLS,
)
from services.screener_service import status_cell


def _build_display_pdf(filtered: pl.DataFrame, visible_metrics: list[str]) -> pd.DataFrame:
    """Project + rename + percent-scale the screener frame to match the AgGrid display."""
    pdf = filtered.to_pandas()

    # Per-source tracking glyphs (✓ / ✗ / —) so the user can see which datasets are
    # missing per fund without leaving the screener.
    pdf["NAV"] = pdf["nav_status"].apply(status_cell) if "nav_status" in pdf.columns else "—"
    pdf["Holdings"] = (
        pdf["holdings_status"].apply(status_cell) if "holdings_status" in pdf.columns else "—"
    )
    pdf["Metadata"] = (
        pdf["metadata_status"].apply(status_cell) if "metadata_status" in pdf.columns else "—"
    )

    # DB-named columns → display names, ordered as in METRIC_RENAME so they don't shuffle.
    db_cols_in_order = [c for c in METRIC_RENAME if c in pdf.columns]
    extra_status = [c for c in ("NAV", "Holdings", "Metadata") if c in pdf.columns]
    pdf = pdf[db_cols_in_order + extra_status].rename(columns=METRIC_RENAME)

    # Visibility filter: identity always shown + user-selected metrics.
    visible = [c for c in IDENTITY_COLS if c in pdf.columns] + [
        c for c in visible_metrics if c in pdf.columns
    ]
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

    # Multi-row selection — header checkbox = select-all, per-row checkbox in Scheme column.
    gob.configure_selection(selection_mode="multiple", use_checkbox=True, header_checkbox=True)
    gob.configure_column(
        "Scheme", pinned="left", minWidth=300, checkboxSelection=True, headerCheckboxSelection=True
    )

    gob.configure_grid_options(
        domLayout="normal",
        suppressHorizontalScroll=False,
        alwaysShowVerticalScroll=True,
        enableCellTextSelection=True,  # cells are text-selectable for native browser copy
        ensureDomOrder=True,             # Cmd/Ctrl+C honours visual order, not insertion order
        suppressCopyRowsToClipboard=False,
        copyHeadersToClipboard=True,     # include column names in the clipboard payload
    )
    return gob.build()


def render_table(
    filtered: pl.DataFrame,
    visible_metrics: list[str],
    aggrid_theme: Any,
) -> tuple[pd.DataFrame, dict]:
    """Build + render the AgGrid table. Returns the display DataFrame (so other sections
    can reuse it without rebuilding) and the AgGrid response (for selection echo).
    """
    display_pdf = _build_display_pdf(filtered, visible_metrics)
    grid_options = _build_grid_options(display_pdf)
    grid_response = AgGrid(
        display_pdf,
        gridOptions=grid_options,
        height=650,
        theme=aggrid_theme,
        allow_unsafe_jscode=False,
    )
    return display_pdf, grid_response


def render_selection_echo(grid_response: dict) -> None:
    """Render an expander with the selected rows as TSV — paste-ready for Excel / Sheets."""
    selected = grid_response.get("selected_rows")
    if selected is None or len(selected) == 0:
        return
    sel_df = pd.DataFrame(selected) if isinstance(selected, list) else selected
    with st.expander(f"📋 {len(sel_df)} selected row(s) — copy", expanded=False):
        st.caption(
            "Tab-separated; paste straight into Excel / Sheets / Notion. The checkbox in "
            "the Scheme column drives the selection (Shift+click ranges, Cmd/Ctrl+click "
            "individual rows)."
        )
        st.code(sel_df.to_csv(sep="\t", index=False), language="text")
