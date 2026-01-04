import streamlit as st
import polars as pl
import pandas as pd
import plotly.express as px
from src.mutualFunds.data_store import ensure_nav_data, ensure_holdings_data
from ui.components.fund_picker import fund_picker
from src.mutualFunds.registry import (
    load_registry,
    save_to_registry,
    fetch_scheme_registry,
)
from src.mutualFunds.analytics import rolling_return_summary, rolling_returns
from ui.charts.indicator_chart import render_indicator
from ui.charts.correlation_heatmap import render_correlation_heatmap


def render_holdings_table(
    holdings_df: pl.DataFrame,
    sector_df: pl.DataFrame,
    assets_df: pl.DataFrame,
    scheme_slug,
    top_n=30,
):
    st.subheader(f"📋 Portfolio Breakdown – {scheme_slug}")

    # --------------------
    # Holdings (Top N + Others)
    # --------------------
    h = (
        holdings_df
        .filter(pl.col("schemeSlug") == scheme_slug)
        .sort("weight", descending=True)
        .select("stockName", "weight")
    )

    top = h.head(top_n)
    others_weight = h.tail(h.height - top_n)["weight"].sum()

    if others_weight > 0:
        top = pl.concat([
            top,
            pl.DataFrame({
                "stockName": ["Others"],
                "weight": [others_weight],
            })
        ])

    # --------------------
    # Sector allocation
    # --------------------
    s = (
        sector_df
        .filter(pl.col("schemeSlug") == scheme_slug)
        .select("sector", "weight")
        .sort("weight", descending=True)
    )

    # --------------------
    # Asset allocation
    # --------------------
    a = (
        assets_df
        .filter(pl.col("schemeSlug") == scheme_slug)
        .select("assetClass", "weight")
        .sort("weight", descending=True)
    )

    # --------------------
    # Layout
    # --------------------
    col1, col2, col3 = st.columns([2.2, 1.2, 1.2])

    # ---- Holdings treemap
    with col1:
        fig_holdings = px.treemap(
            top.to_pandas(),
            path=["stockName"],
            values="weight",
            title="Holdings (Top Constituents)",
        )
        fig_holdings.update_traces(root_color="lightgrey")
        st.plotly_chart(fig_holdings,width='stretch')

    # ---- Sector donut
    with col2:
        fig_sector = px.pie(
            s.to_pandas(),
            names="sector",
            values="weight",
            hole=0.55,
            title="Sector Allocation",
        )
        fig_sector.update_traces(textposition="inside", textinfo="percent")
        st.plotly_chart(fig_sector, width='stretch')

    # ---- Asset donut
    with col3:
        fig_asset = px.pie(
            a.to_pandas(),
            names="assetClass",
            values="weight",
            hole=0.55,
            title="Asset Allocation",
        )
        fig_asset.update_traces(textposition="inside", textinfo="percent")
        st.plotly_chart(fig_asset, width='stretch')

    # --------------------
    # Optional: Data table (collapsed)
    # --------------------
    with st.expander("📄 View holdings table"):
        st.dataframe(
            h.select("stockName", "weight"),
            width='stretch',
        )


def render_pct_change_comparison(nav_pd: pd.DataFrame):
    pct = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change()
        .dropna()
    )

    pct_reset = pct.reset_index().melt(
        id_vars="date", var_name="Scheme", value_name="Return"
    )

    fig = px.line(
        pct_reset,
        x="date",
        y="Return",
        color="Scheme",
        title="Daily % Change Comparison",
    )

    fig.update_yaxes(tickformat=".2%")

    st.plotly_chart(fig, width="stretch")


def show_rolling_returns_info(selected_registry: pl.DataFrame, nav_df:pl.DataFrame):
    
    nav_df = nav_df.join(selected_registry, on="schemeName", how="inner")
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
    if nav_pd.empty:
        st.warning("Select schemes first")
        return

    rr = rolling_returns(nav_pd, rolling_window)

    st.subheader("📊 Rolling Returns")

    left, right = st.columns([2, 3])

    with left:
        for col in rr.columns:
            series = rr[col].dropna()
            if not series.empty:
                render_indicator(f"{col} ({window_label})", series)

    with right:
        st.subheader("📋 Rolling Return Summary")
        rr_summary = rolling_return_summary(rr)
        st.dataframe(rr_summary, width="stretch")


def show_holdings_data(selected_registry):
    st.subheader("🏦 Fund Holdings")
    selected_slugs = selected_registry["schemeSlug"].to_list()
    holdings_df, sectors_df, assets_df = ensure_holdings_data(selected_slugs)
    # st.dataframe(sectors_df)
    for scheme in selected_registry["schemeSlug"]:
        with st.expander(scheme, expanded=False):
            render_holdings_table(holdings_df, sectors_df, assets_df, scheme)


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

def show_correlation_heatmap(selected_registry:pl.DataFrame, nav_pd:pl.DataFrame):
    st.subheader("🔥 Correlation Heatmap")
    nav_df = nav_pd.join(selected_registry, on="schemeName", how="inner")
    nav_pd = nav_df.to_pandas()
    corr = (
        nav_pd
        .pivot(index="date", columns="schemeName", values="nav")
        .pct_change(fill_method=None)
        .dropna()
        .corr()
    )
    st.dataframe(corr)

    render_correlation_heatmap(corr)

def render():

    st.title("💼 Mutual Funds")

    fund_picker(
        fetch_suggestions=fetch_scheme_registry,
        load_registry=load_registry,
        save_to_registry=save_to_registry,
    )
    selected_registry = get_selected_registry(load_registry)

    st.data_editor(selected_registry)

    selected_scheme_names = selected_registry["schemeName"].to_list()
    nav_df = ensure_nav_data(selected_scheme_names)
    show_holdings_data(selected_registry)
    show_rolling_returns_info(selected_registry, nav_df)


    show_correlation_heatmap(selected_registry, nav_df)




render()
