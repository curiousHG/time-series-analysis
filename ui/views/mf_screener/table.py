"""MF Screener table — projects the screener frame to display columns and renders it via
the shared screener grid; owns the click-to-open-fund action."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import streamlit as st

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl

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
from ui.components.screener_grid import clicked_cell_value, render_screener_grid
from ui.state.navigation import open_fund_in_analysis

__all__ = ["render_open_action", "render_table"]


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


def render_table(
    filtered: pl.DataFrame,
    visible_metrics: list[str],
    aggrid_theme: Any,
) -> tuple[pd.DataFrame, dict]:
    """Build + render the AgGrid table. Returns the display DataFrame (reusable without
    rebuilding) and the AgGrid response (for selection echo)."""
    display_pdf = _build_display_pdf(filtered, visible_metrics)
    grid_response = render_screener_grid(
        display_pdf,
        numeric_cols=METRIC_NUMERIC_COLS,
        text_cols=METRIC_TEXT_COLS,
        theme=aggrid_theme,
        pinned_col="Scheme",
        link_col="Scheme",
    )
    return display_pdf, grid_response


def render_open_action(grid_response: dict) -> None:
    """Open a fund in MF Analysis when its Scheme cell is clicked: register it, pull NAV +
    metadata, pre-select, switch pages. Holdings deferred to the Analysis page on load."""
    scheme_name = clicked_cell_value(grid_response, "Scheme")
    if scheme_name is None:
        return

    with st.spinner(f"Fetching NAV + metadata for {scheme_name}…"):
        backfill_missing(
            scheme_names=[scheme_name],
            sources=("nav", "metadata"),
            max_per_run=2,
            submit_delay=0.0,  # single fund, no rate limiting needed
        )
    open_fund_in_analysis(scheme_name)
