import streamlit as st
import polars as pl
import plotly.express as px




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
        .select("instrumentName", "weight")
    )

    top = h.head(top_n)
    others_weight = h.tail(h.height - top_n)["weight"].sum()

    if others_weight > 0:
        top = pl.concat([
            top,
            pl.DataFrame({
                "instrumentName": ["Others"],
                "weight": [others_weight],
            })
        ])

    # --------------------
    # Sector allocation
    # --------------------

    # %7B%22v%22%3A%201%2C%20%22data%22%3A%20%5B%5D%7D
    
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
            path=["instrumentName"],
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
        if s.height>0:
            fig_sector = px.pie(
                s.to_pandas(),
                names="sector",
                values="weight",
                hole=0.55,
                title="Sector Allocation",
            )
            fig_sector.update_traces(textposition="inside", textinfo="percent")
            st.plotly_chart(fig_sector, width='stretch')
        else:
            st.write("No sector data available")

    # ---- Asset donut
    with col3:
        if a.height > 0:
            fig_asset = px.pie(
                a.to_pandas(),
                names="assetClass",
                values="weight",
                hole=0.55,
                title="Asset Allocation",
            )
            fig_asset.update_traces(textposition="inside", textinfo="percent")
            st.plotly_chart(fig_asset, width='stretch')
        else:
            st.write("No asset data available")

    # --------------------
    # Optional: Data table (collapsed)
    # --------------------
    with st.expander("📄 View holdings table"):
        st.dataframe(
            h.select("instrumentName", "weight"),
            width='stretch',
        )

