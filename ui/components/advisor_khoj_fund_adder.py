import streamlit as st
import polars as pl

from data.fetchers.mutual_fund import fetch_scheme_registry
from data.store.mutualfund import save_to_registry
from data.store.mutualfund import load_registry


def advisor_khoj_fund_adder():
    st.sidebar.subheader("🔍 Search AdvisorKhoj")

    query = st.sidebar.text_input(
        "Search mutual funds",
        placeholder="Type fund name (e.g. Parag Parikh, SBI, ICICI...)",
    )

    if not query:
        return

    with st.spinner("Fetching from AdvisorKhoj…"):
        ak_df = fetch_scheme_registry(query)

    if ak_df.is_empty():
        st.sidebar.info("No funds found.")
        return

    registry = load_registry()
    registry_names = set(registry["schemeName"].to_list())

    # annotate whether already present
    display_df = ak_df.with_columns(
        pl.col("schemeName").is_in(registry["schemeName"]).alias("already_in_registry")
    )

    st.caption("AdvisorKhoj results")
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )

    selected = st.multiselect(
        "Select funds to add / use",
        options=display_df["schemeName"].to_list(),
    )

    if not selected:
        return

    # compute what will actually be added
    to_add = [n for n in selected if n not in registry_names]

    if st.button("Add selected to registry"):
        save_to_registry(selected)  # safe to pass all
        if to_add:
            st.success(f"Added {len(to_add)} new fund(s) to registry")
        else:
            st.info("All selected funds were already in registry")
        st.rerun()
