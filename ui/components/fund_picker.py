import streamlit as st
import polars as pl
from ui.data.loaders import cached_search
from ui.persistence.cookies import get_cookie, set_cookie


def fund_picker(
    load_registry,
    save_to_registry,
) -> pl.DataFrame:
    """
    Fund picker with:
    - registry-backed multiselect
    - AdvisorKhoj search + dropdown
    - persistent selection
    """

    if "selected_schemes" not in st.session_state:
        st.session_state.selected_schemes = get_cookie("selected_schemes", []) or []
        st.session_state.allow_persist = False

    if "ak_results" not in st.session_state:
        st.session_state.ak_results = []

    st.sidebar.markdown("### 🔍 Select MFs To Analyze")

    # ---- base options from registry
    registry_names = load_registry()["schemeName"].to_list()

    options = sorted(set(registry_names) | set(st.session_state.selected_schemes))
    st.sidebar.multiselect(
        "Selected Funds",
        options=options,
        key="selected_schemes",
    )
    # ---------- AdvisorKhoj search ----------
    st.sidebar.markdown("### ➕ Add more funds")

    query = st.sidebar.text_input(
        "Search fund name",
        placeholder="Type fund name…",
        key="ak_query",
    )

    if query and len(query) >= 3:
        with st.spinner("Searching…"):
            ak_df = cached_search(query)
            st.session_state.ak_results = ak_df["schemeName"].to_list()
    else:
        st.session_state.ak_results = []

    if st.session_state.ak_results:
        to_add = st.sidebar.multiselect(
            "Search results",
            options=st.session_state.ak_results,
            key="ak_selected",
        )

        if st.sidebar.button("Add selected"):
            # add to registry (safe even if duplicates)
            save_to_registry(to_add)

            st.toast(f"Added {len(to_add)} fund(s)")
            st.rerun()

    if not st.session_state.allow_persist:
        st.session_state.allow_persist = True
        return st.session_state.selected_schemes

    # ---- persist newly added funds
    newly_added = set(st.session_state.selected_schemes) - set(registry_names)
    if newly_added:
        save_to_registry(list(newly_added))
        st.toast(f"Added {len(newly_added)} fund(s)")

    selected_schemes = st.session_state.selected_schemes

    set_cookie("selected_schemes", selected_schemes)

    return selected_schemes
