import polars as pl
import streamlit as st


def make_arrow_safe(df):
    df = df.copy()
    for col in df.columns:
        if "timedelta" in str(df[col].dtype):
            df[col] = df[col].astype(str)
    return df


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
