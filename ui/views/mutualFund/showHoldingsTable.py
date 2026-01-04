import streamlit as st
import polars as pl
import plotly.express as px

from src.mutualFunds.data_store import ensure_holdings_data


def show_holdings_data(selected_scheme_slugs, holdings_df, sectors_df, assets_df):
    st.subheader("🏦 Fund Holdings")
    # st.dataframe(sectors_df)
    for scheme in selected_scheme_slugs:
        with st.expander(scheme, expanded=False):
            render_holdings_table(holdings_df, sectors_df, assets_df, scheme)

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

        fig_holdings.update_traces(
            textinfo="label",
            
            # textfont=dict(size=22, color="black"),
            tiling=dict(
                packing="squarify",
                pad=6,
            ),
            hovertemplate="<b>%{label}</b><br>Weight: %{value:.2f}%<extra></extra>",
        )

        fig_holdings.update_layout(
            height=650,
        )

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

