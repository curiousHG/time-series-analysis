import streamlit as st
import pandas as pd
import polars as pl
import random
from data.store.mutualfund import (
    ensure_fund_mapping,
    ensure_nav_data,
    persist_fund_mapping,
)
from data.store.mutualfund import load_registry
from mutual_funds.tradebook import apply_fund_mapping, compute_daily_units
from ui.charts.fund_trade_comp import fund_trade_comp
from ui.data.loaders import get_trade_symbols, load_nav_data, load_txn_data
from ui.persistence.cookies import set_cookie
from ui.utils import timed
import plotly.graph_objects as go

def init_fund_mapping(trade_symbols: list[str]):
    if "fund_mapping" not in st.session_state:
        loaded = ensure_fund_mapping()
        if loaded is not None:
            st.session_state.fund_mapping = loaded
        else:
            df = pd.DataFrame(
                {
                    "Trade Symbol": trade_symbols,
                    "Mapped NAV Fund": [""] * len(trade_symbols),
                }
            )
            st.session_state.fund_mapping = df
        return

    df = st.session_state.fund_mapping

    existing = set(df["Trade Symbol"])
    incoming = set(trade_symbols)

    # ➕ add new trade symbols
    to_add = incoming - existing
    if to_add:
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    {
                        "Trade Symbol": list(to_add),
                        "Mapped NAV Fund": [""] * len(to_add),
                    }
                ),
            ],
            ignore_index=True,
        )

    # ➖ drop removed symbols
    df = df[df["Trade Symbol"].isin(incoming)]

    st.session_state.fund_mapping = df.reset_index(drop=True)


def update_mapping():
    """
    Applies editor diffs to session_state.fund_mapping
    and persists immediately.
    """
    editor_state = st.session_state.get("fund_mapping_editor")
    if not editor_state:
        return

    df = st.session_state.fund_mapping.copy()

    # ---- apply edited rows
    for row_idx, changes in editor_state.get("edited_rows", {}).items():
        for col, val in changes.items():
            df.at[row_idx, col] = val

    # ---- apply added rows (rare for your case)
    for row in editor_state.get("added_rows", []):
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    # ---- apply deleted rows
    if editor_state.get("deleted_rows"):
        df = df.drop(editor_state["deleted_rows"]).reset_index(drop=True)

    # ---- persist + update state
    st.session_state.fund_mapping = df
    persist_fund_mapping(df)

    st.toast("Mapping saved", icon="💾")


def render_fund_mapping_editor(nav_funds: list[str]):
    st.header("🔗 Map Trade Funds to NAV Funds")

    # with st.expander("ℹ️ Instructions", expanded=False):
    #     st.markdown(
    #         """
    #         - Map each **Trade Symbol** from your transaction data to a corresponding **NAV Fund** from the registry.
    #         - Use the dropdowns to select the appropriate NAV Fund for each Trade Symbol.
    #         - Changes are saved automatically when you edit the table.
    #         - Ensure that all Trade Symbols are mapped for accurate analysis.
    #         """
    #     )
    edited_df = st.data_editor(
        st.session_state.fund_mapping,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Trade Symbol": st.column_config.TextColumn(
                disabled=True,
            ),
            "Mapped NAV Fund": st.column_config.SelectboxColumn(
                "Matched NAV Fund",
                options=[""] + sorted(nav_funds),
            ),
        },
        key="fund_mapping_editor",
        on_change=update_mapping,
    )

    st.session_state.fund_mapping = edited_df


def fund_matcher(txn_df: pl.DataFrame):
    # st.dataframe(txn_df)
    trade_symbols = get_trade_symbols(txn_df)
    registry_df = load_registry()
    nav_funds = registry_df["schemeName"].to_list()

    init_fund_mapping(trade_symbols)
    render_fund_mapping_editor(nav_funds)



    res = apply_fund_mapping(txn_df, ensure_fund_mapping()) # gives pl.DataFrame with schemeName column added

    mapped_schemes = res.select("schemeName").filter(pl.col("schemeName").is_not_null()).unique().to_series().to_list()
    if mapped_schemes:
        if "selected_schemes" not in st.session_state:
            st.session_state.selected_schemes = []
        existing = set(st.session_state.selected_schemes)
        new_ones = [s for s in mapped_schemes if s not in existing]
        if new_ones:
            st.session_state.selected_schemes.extend(new_ones)
            st.toast(f"Added {len(new_ones)} mapped funds to selection")
            set_cookie("selected_schemes", st.session_state.selected_schemes)
    
    nav_df = load_nav_data(
        res["schemeName"].unique().to_list()
    )

    nav_df = nav_df.select(
        [
            "schemeName",
            pl.col("date").cast(pl.Date).alias("date"),
            "nav",
        ])
    price_nav_comparison_df = res.with_columns(pl.col("trade_date").alias("date")).join(
        nav_df,
        on=["schemeName", "date"],
        how="left",
    )
    # show only relevant columns and entries for unique isin
    price_nav_comparison_df = price_nav_comparison_df.select([
        "symbol",
        "schemeName",
        "trade_date",
        "price",
        "nav",
    ])
    uniqueCount = price_nav_comparison_df.select(['symbol','schemeName']).unique().shape[0]
    st.write(f"### 📈 Price vs NAV Comparison for {uniqueCount} Funds")
    
    for scheme in price_nav_comparison_df.select(['symbol','schemeName']).unique().iter_rows():
        # print(scheme)
        symbol = scheme[0]
        schemeName = scheme[1]

        with st.expander(f"🔍 {schemeName} ({symbol})", expanded=False):
            
            # select entries for this schemeName in price_nav_comparison_df
            fund_df = price_nav_comparison_df.filter(
                pl.col("schemeName") == schemeName
            ).to_pandas()

            # date range of this fund in price_nav_comparison_df
            date_min = fund_df["trade_date"].min()
            date_max = fund_df["trade_date"].max()
            # add 3 months buffer
            date_min = date_min - pd.DateOffset(months=3)
            date_max = date_max + pd.DateOffset(months=3)

            # select this schemeName in nav_df in the same date range
            nav_scheme_df = nav_df.filter(
                pl.col("schemeName") == schemeName
            ).to_pandas()
            nav_scheme_df = nav_scheme_df[
                (nav_scheme_df["date"] >= date_min) &
                (nav_scheme_df["date"] <= date_max)
            ]

            fund_df = fund_df.sort_values(by=["trade_date"])
            nav_scheme_df = nav_scheme_df.sort_values(by=["date"])

            fig = fund_trade_comp(fund_df, nav_scheme_df, schemeName, symbol)
            st.plotly_chart(fig, use_container_width=True, key=f"{schemeName}-{symbol}-{random.random()}")
        # break

    # Add mapped schemes to selected_schemes

