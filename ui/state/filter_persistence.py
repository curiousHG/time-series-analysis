"""Hydrate/persist helpers for filter widget state backed by selections.json."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import streamlit as st

from ui.persistence.selections import load_selection, save_selection

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def hydrate_filters(persist_key: str, defaults: dict) -> None:
    """Seed missing session-state keys from selections.json, falling back to `defaults`.

    Idempotent; None values are never seeded. Widgets reading a seeded key must omit `default=`/`value=`.
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
    """Sub-category options for the selected categories, pruning stale persisted values. `df` needs `category`
    and `sub_category`."""
    sel_cats = st.session_state.get(cat_key) or []
    source = df.filter(pl.col("category").is_in(sel_cats)) if sel_cats else df
    options = sorted(source["sub_category"].drop_nulls().unique().to_list())
    if sub_cat_key in st.session_state:
        st.session_state[sub_cat_key] = [s for s in st.session_state[sub_cat_key] if s in options]
    return options
