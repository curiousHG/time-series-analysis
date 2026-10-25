import plotly.express as px
import polars as pl
import streamlit as st


def render_holdings_table(
    holdings_df: pl.DataFrame,
    sector_df: pl.DataFrame,
    assets_df: pl.DataFrame,
    scheme_slug,
    top_n=30,
    display_name: str | None = None,
):
    st.subheader(f"📋 Portfolio Breakdown - {display_name or scheme_slug}")

    h = (
        holdings_df.filter(pl.col("schemeSlug") == scheme_slug)
        .sort("weight", descending=True)
        .select("instrumentName", "weight")
    )

    top = h.head(top_n)
    others_weight = h.tail(h.height - top_n)["weight"].sum()

    if others_weight > 0:
        top = pl.concat(
            [
                top,
                pl.DataFrame(
                    {
                        "instrumentName": ["Others"],
                        "weight": [others_weight],
                    }
                ),
            ]
        )

    s = sector_df.filter(pl.col("schemeSlug") == scheme_slug).select("sector", "weight").sort("weight", descending=True)

    a = (
        assets_df.filter(pl.col("schemeSlug") == scheme_slug)
        .select("assetClass", "weight")
        .sort("weight", descending=True)
    )

    col1, col2, col3 = st.columns([2.2, 1.2, 1.2])

    with col1:
        fig_holdings = px.treemap(
            top.to_pandas(),
            path=["instrumentName"],
            values="weight",
            title="Holdings (Top Constituents)",
        )

        fig_holdings.update_traces(
            textinfo="label",
            tiling=dict(
                packing="squarify",
                pad=6,
            ),
            hovertemplate="<b>%{label}</b><br>Weight: %{value:.2f}%<extra></extra>",
        )

        fig_holdings.update_layout(
            height=650,
        )

        st.plotly_chart(fig_holdings, use_container_width=True, key=f"holdings-tree-{scheme_slug}")

    with col2:
        if s.height > 0:
            fig_sector = px.pie(
                s.to_pandas(),
                names="sector",
                values="weight",
                hole=0.55,
                title="Sector Allocation",
            )
            fig_sector.update_traces(textposition="inside", textinfo="percent")
            st.plotly_chart(fig_sector, use_container_width=True, key=f"holdings-sector-{scheme_slug}")
        else:
            st.write("No sector data available")

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
            st.plotly_chart(fig_asset, use_container_width=True, key=f"holdings-asset-{scheme_slug}")
        else:
            st.write("No asset data available")

    with st.expander("📄 View holdings table"):
        st.dataframe(
            h.select("instrumentName", "weight"),
            width="stretch",
        )
