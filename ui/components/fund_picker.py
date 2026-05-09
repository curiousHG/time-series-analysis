import logging

import streamlit as st

from services.portfolio_holdings import get_active_portfolio_schemes
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import cached_search

logger = logging.getLogger("ui.components.fund_picker")

# App state key (not widget-bound): _selected_schemes
# Widget key (owned by multiselect): _schemes_widget


def _init_schemes():
    """Initialize app state on first run.

    Initialize from previously saved selection.
    """
    if "_selected_schemes" in st.session_state:
        return

    st.session_state._selected_schemes = load_selection("selected_schemes", [])


def _on_widget_change():
    """Callback: widget → app state sync."""
    st.session_state._selected_schemes = list(st.session_state._schemes_widget)
    save_selection("selected_schemes", st.session_state._selected_schemes)


def _on_add_funds():
    """Callback: add search results to selection."""
    to_add = st.session_state.get("ak_selected", [])
    if to_add:
        merged = sorted(set(st.session_state._selected_schemes) | set(to_add))
        st.session_state._selected_schemes = merged
        save_selection("selected_schemes", merged)
        logger.info("Added %d funds via search: %s", len(to_add), to_add)


def get_selected_schemes() -> list[str]:
    """Read current selection from anywhere without touching widget state."""
    _init_schemes()
    return st.session_state._selected_schemes


def add_schemes(names: list[str]):
    """Programmatically add schemes (e.g. from fund_matcher). Safe to call anytime."""
    _init_schemes()
    current = set(st.session_state._selected_schemes)
    new_ones = set(names) - current
    if new_ones:
        merged = sorted(current | new_ones)
        st.session_state._selected_schemes = merged
        save_selection("selected_schemes", merged)
        logger.info("Auto-added %d mapped schemes: %s", len(new_ones), list(new_ones))
        return list(new_ones)
    return []


def fund_picker(
    load_registry,
    save_to_registry,
    *,
    use_sidebar: bool = True,
) -> list[str]:
    """
    Fund picker with:
    - registry-backed multiselect
    - AdvisorKhoj search + dropdown
    - persistent selection via local file
    """
    _init_schemes()

    # If defaults were just loaded, clear the widget cache so it picks up new values
    if st.session_state.pop("_load_fund_defaults", False):
        st.session_state.pop("_schemes_widget", None)

    if "ak_results" not in st.session_state:
        st.session_state.ak_results = []

    ui = st.sidebar if use_sidebar else st
    ui.markdown("### Select MFs To Analyze")

    # ---- base options from registry
    registry_df = load_registry()
    registry_names = registry_df["schemeName"].to_list()
    name_to_short = (
        dict(zip(registry_df["schemeName"].to_list(), registry_df["shortName"].to_list(), strict=False))
        if "shortName" in registry_df.columns
        else {}
    )

    def _label(name: str) -> str:
        return name_to_short.get(name, name)

    options = sorted(set(registry_names) | set(st.session_state._selected_schemes))

    # Default value comes from app state; widget syncs back via callback
    default = [s for s in st.session_state._selected_schemes if s in options]

    ui.multiselect(
        "Selected Funds",
        options=options,
        default=default,
        key="_schemes_widget",
        on_change=_on_widget_change,
        format_func=_label,
    )

    # ---------- AMFI fuzzy search ----------
    ui.markdown("### Add more funds")

    query = ui.text_input(
        "Search fund name (AMFI universe, ~14K schemes)",
        placeholder="e.g. parag parikh flexi cap",
        key="ak_query",
    )

    if query and len(query) >= 2:
        with st.spinner("Searching AMFI…"):
            ak_df = cached_search(query)
            st.session_state.ak_results = ak_df["schemeName"].to_list()
    else:
        st.session_state.ak_results = []

    if st.session_state.ak_results:
        ui.multiselect(
            "Search results",
            options=st.session_state.ak_results,
            key="ak_selected",
            format_func=_label,
        )

        ui.button("Add selected", on_click=_on_add_funds)

    # Auto-save new funds to registry
    newly_added = set(st.session_state._selected_schemes) - set(registry_names)
    if newly_added:
        save_to_registry(list(newly_added))

    # ---- Defaults
    ui.divider()
    dc1, dc2 = ui.columns(2)
    if dc1.button("Save as default", key="save_default_funds", use_container_width=True):
        save_selection("default_schemes", st.session_state._selected_schemes)
        ui.success(f"Saved {len(st.session_state._selected_schemes)} funds as default")
    if dc2.button("Load portfolio", key="load_portfolio_funds", use_container_width=True):
        portfolio = get_active_portfolio_schemes()
        if portfolio:
            st.session_state._selected_schemes = portfolio
            save_selection("selected_schemes", portfolio)
            st.session_state._load_fund_defaults = True
            st.rerun()
        else:
            ui.warning("No active portfolio holdings — upload tradebook in Data Manager")

    return st.session_state._selected_schemes
