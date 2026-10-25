"""Drain the core.notifications bus and surface entries as toasts.

Call `render_toasts()` right after a page's data load.
"""

from __future__ import annotations

import streamlit as st

from core.notifications import drain_notices

_ICONS = {"info": "💡", "success": "✅", "warning": "⚠️", "error": "❌"}


def render_toasts() -> None:
    for notice in drain_notices():
        st.toast(notice.message, icon=_ICONS.get(notice.level, "⚠️"))
