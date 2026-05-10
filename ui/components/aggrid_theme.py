"""Reusable AgGrid theme objects + selectable theme catalog.

streamlit-aggrid 1.x uses AgGrid v32's parametric theming: build a theme by composing
`StAggridTheme(base=...)` + `.withParams(...)`. The plain string names (`alpine-dark`,
`balham-dark`) silently fall back to light renders in this version — only `streamlit`,
`alpine`, `balham`, `material` are accepted as bare strings, and none of those are dark
by default. So the dark theme has to be a real `StAggridTheme` object with explicit
colour parameters.
"""

from __future__ import annotations

from st_aggrid import StAggridTheme


def streamlit_dark_aggrid_theme() -> StAggridTheme:
    """Quartz base re-skinned to match Streamlit's dark theme palette.

    Rows take the page-background black (`#0e1117`); the header band uses Streamlit's
    secondary-surface grey (`#262730`) — same shade as the sidebar / `st.container(border=True)`
    — so the table reads as a contained card on the page.
    """
    return StAggridTheme(base="quartz").withParams(
        browserColorScheme="dark",
        backgroundColor="#0e1117",
        foregroundColor="#fafafa",
        accentColor="#ff4b4b",
        borderColor="#3a3d4a",
        chromeBackgroundColor="#1c1d24",
        headerBackgroundColor="#262730",
        headerTextColor="#fafafa",
        oddRowBackgroundColor="#0e1117",
        rowHoverColor="#262730",
        selectedRowBackgroundColor="#3a3d4a",
        wrapperBorder=True,
        fontFamily="'Source Sans Pro', sans-serif",
        fontSize=13,
        headerFontSize=13,
        headerFontWeight=600,
    )


# Selectable themes for the screener (and any other AgGrid-using view). Mapping is
# label → theme value (a `StAggridTheme` for the custom variant; a plain string for
# the bare AgGrid built-ins).
AGGRID_THEMES: dict[str, object] = {
    "Streamlit dark (custom)": streamlit_dark_aggrid_theme(),
    "streamlit": "streamlit",
    "alpine": "alpine",
    "balham": "balham",
    "material": "material",
}
DEFAULT_AGGRID_THEME = "Streamlit dark (custom)"
