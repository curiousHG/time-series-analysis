import streamlit as st
import polars as pl

from ui.components.cookies import get_cookie, set_cookie
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
    load_registry,
    save_to_registry,
):
    """
    Fund picker component with autosuggest + persistent selection.
    Returns: list[str] of selected scheme names
    """

    if "selection_initialized" not in st.session_state:
        stored = get_cookie("selected_schemes", None)

        # Only restore if cookie actually exists
        if stored is not None:
            st.session_state.selected_schemes = stored
        else:
            st.session_state.selected_schemes = []

        st.session_state.selection_initialized = True
        st.session_state.allow_persist = False



    st.sidebar.markdown("## 🔍 Select Mutual Funds")


    # ---- base options from registry
    registry_names = load_registry()["schemeName"].to_list()


    # ---- merged option set (CRITICAL)
    options = sorted(
        set(registry_names)
        | set(st.session_state.selected_schemes)
    )
    # ---- multiselect = search + add + remove
    st.sidebar.multiselect(
        "Funds",
        options=options,
        key="selected_schemes",
    )

    if not st.session_state.allow_persist:
        st.session_state.allow_persist = True
        return st.session_state.selected_schemes

    # ---- persist newly added funds
    newly_added = set(st.session_state.selected_schemes) - set(registry_names)
    if newly_added:
        save_to_registry(list(newly_added))
        st.toast(f"Added {len(newly_added)} fund(s)")

    set_cookie("selected_schemes", st.session_state.selected_schemes)

    return st.session_state.selected_schemes
