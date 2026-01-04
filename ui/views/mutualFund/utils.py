import polars as pl
import streamlit as st

def get_selected_registry(load_registry) -> pl.DataFrame:
    """
    Returns full registry rows for currently selected schemes
    stored in st.session_state.selected_schemes
    """
    registry = load_registry()

    if not st.session_state.selected_schemes:
        return registry.head(0)  # empty df, same schema

    return registry.filter(
        pl.col("schemeName").is_in(st.session_state.selected_schemes)
    )