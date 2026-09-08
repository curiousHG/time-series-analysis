"""App-wide chrome: the global stylesheet and the shared page header.

The theme itself lives in .streamlit/config.toml; this file only tightens spacing that the theme
cannot reach and gives every page the same title row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    from collections.abc import Callable

_GLOBAL_CSS = """
<style>
.block-container { padding-top: 3.6rem; padding-bottom: 2.5rem; }
h1, h2, h3 { letter-spacing: -0.01em; }
.page-title { font-size: 1.45rem; font-weight: 600; line-height: 1.2; margin: 0; }
.page-subtitle { color: #94a3b8; font-size: 0.9rem; margin: 0.15rem 0 0.6rem; }
.section-title { font-size: 1.05rem; font-weight: 600; margin: 0; }
.section-note { color: #94a3b8; font-size: 0.85rem; margin: 0.1rem 0 0; }
div[data-testid="stTabs"] button p { font-size: 0.9rem; font-weight: 500; }
div[data-testid="stMetric"] { padding: 0; }
div[data-testid="stMetricLabel"] p { color: #94a3b8; font-size: 0.8rem; }
div[data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 500; letter-spacing: -0.01em; }
div[data-testid="stMetricDelta"] { font-size: 0.8rem; }
div[data-testid="stCaptionContainer"] p { color: #94a3b8; }
div[data-testid="stAlert"] { padding: 0.55rem 0.8rem; }
div[data-testid="stAlert"] p { font-size: 0.9rem; }
hr { margin: 1.2rem 0; }
div[data-testid="stDataFrame"] { font-size: 0.85rem; }
</style>
"""


def inject_global_css() -> None:
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None, actions: Callable[[], None] | None = None) -> None:
    """One title row per page: title and context on the left, an optional control on the right."""
    left, right = st.columns([11, 1], vertical_alignment="center")
    with left:
        html = f'<p class="page-title">{title}</p>'
        if subtitle:
            html += f'<p class="page-subtitle">{subtitle}</p>'
        st.markdown(html, unsafe_allow_html=True)
    if actions is not None:
        with right:
            actions()


def section_header(title: str, note: str | None = None):
    """A section title with an optional note on the left; returns the right-hand column so the
    caller can place the section's action controls beside the title."""
    left, right = st.columns([8, 4], vertical_alignment="center")
    with left:
        html = f'<p class="section-title">{title}</p>'
        if note:
            html += f'<p class="section-note">{note}</p>'
        st.markdown(html, unsafe_allow_html=True)
    return right
