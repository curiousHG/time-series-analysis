"""Reusable AgGrid theme objects and the selectable theme catalog."""

from __future__ import annotations

from st_aggrid import StAggridTheme


def streamlit_dark_aggrid_theme() -> StAggridTheme:
    """Quartz base re-skinned to Streamlit's dark palette."""
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


AGGRID_THEMES: dict[str, object] = {
    "Streamlit dark (custom)": streamlit_dark_aggrid_theme(),
    "streamlit": "streamlit",
    "alpine": "alpine",
    "balham": "balham",
    "material": "material",
}
DEFAULT_AGGRID_THEME = "Streamlit dark (custom)"
