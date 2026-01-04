import streamlit as st
import polars as pl

from ui.components.localstorage import load_from_local_storage, save_to_local_storage

def _add_fund(scheme, load_registry, save_to_registry):
    if scheme not in st.session_state.selected_schemes:
        st.session_state.selected_schemes.append(scheme)

    registry = load_registry()
    if scheme not in registry["schemeName"].to_list():
        save_to_registry([scheme])
        st.cache_data.clear()
        st.toast(f"Added {scheme}")

    # st.sidebar.success("Added")
    # st.rerun()
    

def fund_picker(
    fetch_suggestions,
    load_registry,
    save_to_registry,
):
    """
    Fund picker component with autosuggest + persistent selection.
    Returns: list[str] of selected scheme names
    """

    st.session_state.setdefault(
        "selected_schemes",
        load_from_local_storage("selected_schemes", [])
    )

    st.sidebar.markdown("## 🔍 Select Mutual Funds")

    # ---- search box (only for suggestions)
    query = st.sidebar.text_input(
        "Search funds",
        key="search_query",
        placeholder="Type fund name…",
    )

    # ---- base options from registry
    registry_names = load_registry()["schemeName"].to_list()

    # ---- live suggestions
    suggestions = []
    if len(query) >= 2:
        suggestions = fetch_suggestions(query)

    # ---- merged option set (CRITICAL)
    options = sorted(
        set(registry_names)
        | set(suggestions)
        | set(st.session_state.selected_schemes)
    )


    # ---- multiselect = search + add + remove
    selected = st.sidebar.multiselect(
        "Funds",
        options=options,
        default=st.session_state.selected_schemes,
        key="selected_schemes",
    )

    # ---- persist newly added funds
    newly_added = set(selected) - set(registry_names)
    if newly_added:
        save_to_registry(list(newly_added))
        st.toast(f"Added {len(newly_added)} fund(s)")
    save_to_local_storage("selected_schemes", selected)

    return selected
