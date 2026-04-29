import time
from contextlib import contextmanager

import polars as pl
import streamlit as st


def get_selected_registry(load_registry) -> pl.DataFrame:
    """
    Returns full registry rows for currently selected schemes.
    """
    from ui.components.fund_picker import get_selected_schemes

    registry = load_registry()
    selected = get_selected_schemes()

    if not selected:
        return registry.head(0)  # empty df, same schema

    return registry.filter(pl.col("schemeName").is_in(selected))


@contextmanager
def timed(label: str):
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    st.sidebar.write(f"⏱️ {label}: {elapsed:.2f}s")
