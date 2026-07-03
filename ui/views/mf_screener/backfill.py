"""Add-to-tracked + fetch-data controls for the MF Screener. Picks the top-N rows *as
displayed in the grid* (client-side sort + header filters), upserts to mf_registry,
fetches NAV + metadata, then clears caches on success."""

from __future__ import annotations

import polars as pl
import streamlit as st

from services.registry_service import backfill_missing
from ui.constants import BACKFILL_HELP_TEXT
from ui.state.loaders import load_metrics_cached, load_screener_df_cached

# Set by the page after the grid renders: scheme names in the order the user actually sees
# (AgGrid client-side sort + floating filters applied). Survives to the next rerun, so when
# the Fetch button (rendered above the grid) fires, we pick what the user was looking at.
DISPLAY_ORDER_KEY = "screener_display_order"


def pick_backfill_names(filtered: pl.DataFrame, displayed: list[str] | None, n: int) -> list[str]:
    """Top-N scheme names to fetch: displayed grid order first, server frame as fallback.

    Displayed names are intersected with the current filtered frame so a stale grid order
    (sidebar filters changed since last render) can't pick rows that are no longer shown.
    """
    valid = set(filtered["scheme_name"].to_list())
    if displayed:
        picked = [s for s in displayed if s in valid][:n]
        if picked:
            return picked
    return filtered["scheme_name"].head(n).to_list()


def render_inline_backfill(filtered: pl.DataFrame, n_col, btn_col) -> None:
    """Render the Top-N input + Fetch button and run the backfill on click. Disabled when
    no rows match the current filter."""
    has_rows = filtered.height > 0
    max_n = min(500, filtered.height) if has_rows else 1
    default_n = min(50, filtered.height) if has_rows else 1

    with n_col:
        batch = st.number_input(
            "Top N",
            min_value=1,
            max_value=max_n,
            value=default_n,
            step=10,
            key="screener_backfill_n",
            disabled=not has_rows,
        )
    with btn_col:
        run_clicked = st.button(
            f"Fetch data for top {int(batch)}",
            type="primary",
            key="screener_backfill",
            use_container_width=True,
            disabled=not has_rows,
            help=BACKFILL_HELP_TEXT,
        )

    if not run_clicked:
        return

    picked_names = pick_backfill_names(filtered, st.session_state.get(DISPLAY_ORDER_KEY), int(batch))
    total_items = int(batch) * 2  # nav + metadata per fund
    progress = st.progress(0.0, text="Starting…")

    def _cb(done: int, total: int, name: str, source: str) -> None:
        progress.progress(done / total, text=f"[{done}/{total}] {source}: {name[:60]}")

    with st.spinner(f"Fetching NAV/metadata for {len(picked_names)} fund(s)…"):
        result = backfill_missing(
            scheme_names=picked_names,
            max_per_run=total_items,
            progress_cb=_cb,
        )
    progress.progress(1.0, text="Done")
    load_screener_df_cached.clear()
    load_metrics_cached.clear()
    st.success(f"Fetched {len(result['fetched'])} · failed {len(result['failed'])}")
    st.rerun()
