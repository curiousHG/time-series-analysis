"""Add-to-tracked + fetch-data controls for the MF Screener. Picks the top-N rows *as
displayed in the grid* (client-side sort + header filters), upserts to mf_registry,
fetches NAV + metadata, then clears caches on success."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from core.background import set_task_progress
from services.registry_service import backfill_missing
from ui.components.background_refresh import BackgroundRefresh
from ui.constants import BACKFILL_HELP_TEXT
from ui.state.loaders import load_metrics_cached, load_screener_df_cached

if TYPE_CHECKING:
    import polars as pl

_BACKFILL_KEY = "mf_screener_backfill"
_REFRESH = BackgroundRefresh(
    _BACKFILL_KEY,
    "MF backfill",
    cache_clearers=(load_screener_df_cached.clear, load_metrics_cached.clear),
    summarize=lambda r: f"fetched {len(r['fetched'])}, failed {len(r['failed'])}" if isinstance(r, dict) else str(r),
)

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
    """Render the Top-N input + Fetch button; the fetch runs on a background task (stale-while-
    revalidate) so the grid stays interactive. Disabled when no rows match the current filter."""
    _REFRESH.consume()
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
            disabled=not has_rows or _REFRESH.is_running(),
        )

    def _task() -> dict:
        names = pick_backfill_names(filtered, st.session_state.get(DISPLAY_ORDER_KEY), int(batch))
        return backfill_missing(
            scheme_names=names,
            max_per_run=int(batch) * 2,  # nav + metadata per fund
            progress_cb=lambda done, total, name, source: set_task_progress(
                _BACKFILL_KEY, phase=source, done=done, total=total
            ),
        )

    with btn_col:
        _REFRESH.start_button(
            f"Fetch data for top {int(batch)}",
            _task,
            type="primary",
            key="screener_backfill",
            disabled=not has_rows or _REFRESH.is_running(),
            help=BACKFILL_HELP_TEXT,
        )
    if _REFRESH.is_running():
        _REFRESH.poll()
