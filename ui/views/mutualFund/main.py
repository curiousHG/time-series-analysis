import streamlit as st
import polars as pl
import pandas as pd

from src.mutualFunds.data_store import ensure_holdings_data, ensure_nav_data
from ui.views.mutualFund.fund_picker import fund_picker
from src.mutualFunds.registry import (
    load_registry,
    save_to_registry,
)
from src.mutualFunds.analytics import overlap_matrix, sector_exposure

from ui.charts.correlation_heatmap import render_correlation_heatmap
from ui.views.mutualFund.showHoldingsTable import show_holdings_data
from ui.views.mutualFund.showRollingReturns import show_rolling_returns_info
from ui.views.mutualFund.tradebook import compute_current_holdings, load_tradebook, normalize_transactions
from ui.views.mutualFund.utils import get_selected_registry
from ui.views.mutualFund.plotters import plot_kde_returns, plot_overlap_heatmap, plot_sector_stack


def show_pct_change_comparison(nav_pd: pd.DataFrame):
    pct = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change()
        .dropna()
    )

    plot_kde_returns(pct)


def show_stock_overlap(
    holdings_df: pl.DataFrame, sector_df: pl.DataFrame, selected_scheme_slugs
):
    st.write("## Stock Overlap")
    matrix = overlap_matrix(holdings_df, selected_scheme_slugs)
    plot_overlap_heatmap(matrix)
    plot_sector_stack(sector_df, selected_scheme_slugs)


def show_correlation_heatmap(selected_registry: pl.DataFrame, nav_pd: pl.DataFrame):
    st.subheader("🔥 Nav Correlation Heatmap")
    nav_df = nav_pd.join(selected_registry, on="schemeName", how="inner")
    nav_pd = nav_df.to_pandas()
    corr = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change(fill_method=None)
        .dropna()
        .corr()
    )
    st.dataframe(corr)

    render_correlation_heatmap(corr)


st.title("💼 Mutual Funds")

selected_schemes = fund_picker(
    load_registry=load_registry,
    save_to_registry=save_to_registry,
)

tradebook = load_tradebook("data/user/tradebook-MF.csv")
txn_df = normalize_transactions(tradebook)

current_holdings = compute_current_holdings(txn_df)
st.dataframe(current_holdings)
# show_current_holdings(current_holdings)

# daily_units = compute_daily_units(txn_df)
# ts_df = compute_portfolio_timeseries(
#     daily_units.rename({"trade_date": "date"}),
#     nav_df,
# )

# portfolio_ts = compute_total_portfolio_value(ts_df)
# show_portfolio_value_chart(portfolio_ts)
# # 🔑 sync portfolio table with selection
# sync_user_portfolio(selected_schemes)

# # ✏️ editable portfolio UI
# render_portfolio_editor()

selected_registry = get_selected_registry(load_registry)

st.data_editor(selected_registry)

selected_scheme_names = selected_registry["schemeName"].to_list()
selected_scheme_slugs = selected_registry["schemeSlug"].to_list()
nav_df = ensure_nav_data(selected_scheme_names)
holdings_df, sectors_df, assets_df = ensure_holdings_data(selected_scheme_slugs)
# st.dataframe(holdings_df)
nav_df = nav_df.join(selected_registry, on="schemeName", how="inner")
nav_pd = nav_df.to_pandas()
show_stock_overlap(holdings_df, sectors_df, selected_scheme_slugs)
show_pct_change_comparison(nav_pd)
show_holdings_data(selected_scheme_slugs, holdings_df, sectors_df, assets_df)
show_rolling_returns_info(selected_registry, nav_df)

show_correlation_heatmap(selected_registry, nav_df)



