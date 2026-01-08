import streamlit as st
import polars as pl

from ui.components.cookies import get_cookie, set_cookie


def fund_picker(
    load_registry,
    save_to_registry,
)->pl.DataFrame:
    """
    Fund picker component with autosuggest + persistent selection.
    Returns: list[str] of selected scheme names
    """

    if "selection_initialized" not in st.session_state:
        stored = get_cookie("selected_schemes", None)

        st.session_state.selected_schemes = stored or []
        st.session_state.selection_initialized = True
        st.session_state.allow_persist = False

    st.sidebar.markdown("## 🔍 Select Mutual Funds")

    # ---- base options from registry
    registry_names = load_registry()["schemeName"].to_list()

    options = sorted(
        set(registry_names)
        | set(st.session_state.selected_schemes)
    )
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

    selected_schemes = st.session_state.selected_schemes

    set_cookie("selected_schemes", selected_schemes)

    return selected_schemes
