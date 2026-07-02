"""Generic hydrate/persist helpers for filter widget state backed by selections.json.

One implementation shared by every page with persisted sidebar filters (MF Screener,
MF Analysis, Stock Screener), replacing per-page copies of the same pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import streamlit as st

from ui.persistence.selections import load_selection, save_selection

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def hydrate_filters(persist_key: str, defaults: dict) -> None:
    """Seed missing session-state keys from selections.json, falling back to `defaults`.

    Idempotent so live edits aren't clobbered; re-seeds each run since Streamlit GCs
    widget keys on page nav. Widgets that read a seeded key must omit `default=`/`value=`.
    `None` values are never seeded (a selectbox must not be pre-seeded with None).
    """
    saved = load_selection(persist_key, {})
    for key, default in defaults.items():
        if key in st.session_state:
            continue
        value = saved.get(key, default)
        if value is not None:
            st.session_state[key] = value


def make_persist_callback(persist_key: str, keys: Iterable[str]) -> Callable[[], None]:
    """Return an `on_change` callback that snapshots `keys` to selections.json."""
    keys = list(keys)

    def _persist() -> None:
        save_selection(persist_key, {k: st.session_state[k] for k in keys if k in st.session_state})

    return _persist


def cascade_subcategory_options(df: pl.DataFrame, *, cat_key: str, sub_cat_key: str) -> list[str]:
    """Sub-category options cascading off the selected categories.

    Prunes stale persisted sub-categories so the keyed multiselect never holds a value
    outside its options. `df` needs `category` and `sub_category` columns.
    """
    sel_cats = st.session_state.get(cat_key) or []
    source = df.filter(pl.col("category").is_in(sel_cats)) if sel_cats else df
    options = sorted(source["sub_category"].drop_nulls().unique().to_list())
    if sub_cat_key in st.session_state:
        st.session_state[sub_cat_key] = [s for s in st.session_state[sub_cat_key] if s in options]
    return options
