"""Drain the Streamlit-free notice bus (core.notifications) and surface entries as toasts.

Call `render_toasts()` on a page right after its data load so notices pushed during a
cache-miss run (e.g. yfinance fetch failures) show that same run and auto-dismiss.
"""

from __future__ import annotations

import streamlit as st

from core.notifications import drain_notices

_ICONS = {"info": "💡", "success": "✅", "warning": "⚠️", "error": "❌"}


def render_toasts() -> None:
    for notice in drain_notices():
        st.toast(notice.message, icon=_ICONS.get(notice.level, "⚠️"))
