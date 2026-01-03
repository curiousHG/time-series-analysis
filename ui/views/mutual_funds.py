import streamlit as st
import polars as pl
import pandas as pd
import plotly.express as px
from src.mf.nav_store import ensure_nav_data
from ui.charts.indicator_chart import render_indicator
from ui.charts.correlation_heatmap import render_correlation_heatmap

@st.cache_data
def load_registry():
    return pl.read_parquet("data/parquet/mf_registry.parquet")

def rolling_return_summary(rr_df: pd.DataFrame):
    return pd.DataFrame({
        "Mean Return %": rr_df.mean() * 100,
        "Min Return %": rr_df.min() * 100,
        "Max Return %": rr_df.max() * 100,
        "Latest %": rr_df.iloc[-1] * 100,
    }).round(2)

def render_holdings_table(holdings_df, scheme_name):
    st.subheader(f"📋 Holdings – {scheme_name}")

    df = (
        holdings_df
        .filter(pl.col("scheme_name") == scheme_name)
        .sort("weight", descending=True)
        .select(["stock_name", "weight"])
    )

    st.dataframe(df.to_pandas())

def compute_overlap(holdings_df, fund_a, fund_b):
    df_a = (
        holdings_df
        .filter(pl.col("scheme_name") == fund_a)
        .select("isin", pl.col("weight").alias("w_a"))
    )

    df_b = (
        holdings_df
        .filter(pl.col("scheme_name") == fund_b)
        .select("isin", pl.col("weight").alias("w_b"))
    )

    merged = df_a.join(df_b, on="isin", how="inner")

    return (
        merged
        .with_columns(pl.min_horizontal(["w_a", "w_b"]).alias("overlap"))
        .select(pl.sum("overlap"))
        .item()
    )

def overlap_matrix(holdings_df, funds):
    matrix = pd.DataFrame(
        index=funds,
        columns=funds,
        dtype=float
    )

    for a in funds:
        for b in funds:
            matrix.loc[a, b] = compute_overlap(
                holdings_df, a, b
            )

    return matrix.round(2)



def rolling_returns(nav_pd: pd.DataFrame, window: int):
    pivot = nav_pd.pivot(
        index="date",
        columns="schemeName",
        values="nav"
    )

    return pivot.pct_change(window)

def render(state):
    st.title("💼 Mutual Funds")

    registry = load_registry()
    # st.write(registry)

    scheme_names = registry["schemeName"].to_list()
    selected_schemes = st.multiselect(
        "Select Mutual Funds",
        options=scheme_names,
    )

    if not selected_schemes:
        st.info("Select one or more mutual funds")
        return

    selected_codes = (
        registry
        .filter(pl.col("schemeName").is_in(selected_schemes))
        .select("schemeCode", "schemeName")
    )

    nav_df = ensure_nav_data(
        selected_codes["schemeCode"].to_list()
    )

    nav_df = nav_df.join(
        selected_codes,
        on="schemeCode",
        how="inner"
    )
    nav_pd = nav_df.to_pandas()
    st.subheader("Analytics")
    ROLLING_WINDOWS = {
        "3 Months": 63,
        "6 Months": 126,
        "1 Year": 252,
        "3 Years": 756,
    }

    window_label = st.selectbox(
        "Rolling Return Window",
        options=list(ROLLING_WINDOWS.keys()),
    )

    rolling_window = ROLLING_WINDOWS[window_label]
    rr = rolling_returns(nav_pd, rolling_window)

    st.subheader("📊 Rolling Returns")

    left, right = st.columns([2, 3])

    with left:
        for col in rr.columns:
            series = rr[col].dropna()
            if not series.empty:
                render_indicator(
                    f"{col} ({window_label})",
                    series
                )

    with right:
        st.subheader("📋 Rolling Return Summary")
        rr_summary = rolling_return_summary(rr)
        st.dataframe(rr_summary, use_container_width=True)

    
    st.subheader("🔥 Correlation Heatmap")

    corr = (
        nav_pd
        .pivot(index="date", columns="schemeName", values="nav")
        .pct_change()
        .dropna()
        .corr()
    )

    render_correlation_heatmap(corr)