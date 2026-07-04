"""Consistent KPI tiles — one call renders a row of `st.metric` with shared formatting.

Replaces the hand-rolled `st.columns(4)` + per-tile f-string blocks repeated across pages.
Missing values (None/NaN) always render as an em-dash.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import streamlit as st

EM_DASH = "—"


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def fmt_pct(value: float | None, *, signed: bool = True, decimals: int = 1) -> str:
    """Decimal fraction → percent string; e.g. 0.123 → '+12.3%'."""
    if value is None or math.isnan(value):
        return EM_DASH
    sign = "+" if signed else ""
    return f"{value * 100:{sign}.{decimals}f}%"


def fmt_ratio(value: float | None, *, decimals: int = 2) -> str:
    if _is_missing(value):
        return EM_DASH
    return f"{value:.{decimals}f}"


def fmt_inr(value: float | None, *, decimals: int = 0) -> str:
    """Plain grouped rupee amount; the caller decides the unit (₹, ₹ Cr, …) via the label."""
    if _is_missing(value):
        return EM_DASH
    return f"{value:,.{decimals}f}"


def fmt_inr_compact(value: float | None) -> str:
    """₹ value → compact lakh/crore form: 1_50_00_000 → '₹1.50 Cr', 2_50_000 → '₹2.50 L'."""
    if value is None or math.isnan(value):
        return EM_DASH
    if abs(value) >= 1e7:
        return f"₹{value / 1e7:,.2f} Cr"
    if abs(value) >= 1e5:
        return f"₹{value / 1e5:,.2f} L"
    return f"₹{value:,.0f}"


_FORMATTERS = {
    "pct": lambda v: fmt_pct(v, signed=True),
    "pct_unsigned": lambda v: fmt_pct(v, signed=False),
    "ratio": fmt_ratio,
    "inr": fmt_inr,
    "inr_compact": fmt_inr_compact,
}


@dataclass(frozen=True)
class Kpi:
    """One metric tile. `fmt` picks a shared formatter; None renders `value` verbatim.

    `pill` is an optional (streamlit-color, text) rating badge rendered under the value — a
    quick good/bad read of what the number means (e.g. ("green", "Good")).
    """

    label: str
    value: Any
    delta: str | None = None
    help: str | None = None
    fmt: str | None = None  # "pct" | "pct_unsigned" | "ratio" | "inr" | "inr_compact"
    pill: tuple[str, str] | None = None  # (color, text) — color ∈ green/orange/red/blue/gray


def render_kpi_row(tiles: list[Kpi], columns: int | None = None) -> None:
    """Render `tiles` as a row of st.metric; wraps onto extra rows past `columns` tiles."""
    if not tiles:
        return
    per_row = columns or len(tiles)
    for start in range(0, len(tiles), per_row):
        row = tiles[start : start + per_row]
        for col, tile in zip(st.columns(per_row), row, strict=False):
            if tile.fmt is not None:
                display = _FORMATTERS[tile.fmt](tile.value)
            else:
                display = EM_DASH if _is_missing(tile.value) else str(tile.value)
            col.metric(tile.label, display, delta=tile.delta, help=tile.help)
            if tile.pill is not None:
                color, text = tile.pill
                col.markdown(f":{color}-background[{text}]")
