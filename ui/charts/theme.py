"""Consistent Plotly theme for the financial dashboard."""

import plotly.graph_objects as go
import plotly.io as pio

COLORS = [
    "#6366f1",
    "#f59e0b",
    "#10b981",
    "#ef4444",
    "#8b5cf6",
    "#06b6d4",
    "#f97316",
    "#ec4899",
]

BG_COLOR = "#0f1117"
PAPER_COLOR = "#0f1117"
GRID_COLOR = "#1e293b"
TEXT_COLOR = "#e2e8f0"
FONT_FAMILY = "Inter, system-ui, sans-serif"

POSITIVE = "#10b981"
POSITIVE_SOFT = "#86efac"
NEGATIVE = "#ef4444"
NEGATIVE_SOFT = "#fca5a5"
NEUTRAL = "#64748b"
QUADRANT_LINE = "#475569"
ACCENT = "#6366f1"
INFO = "#60a5fa"
BENCHMARK_LINE = "#94a3b8"
WARNING = "#fbbf24"
LINK = "#5aa9ff"

COMPACT_MARGIN = {"l": 48, "r": 16, "t": 24, "b": 36}


def with_alpha(hex_color: str, alpha: float) -> str:
    """Translate a 6-digit hex colour into an `rgba(r, g, b, a)` string for fills and soft lines."""
    digits = hex_color.lstrip("#")
    if len(digits) != 6:
        raise ValueError(f"expected a 6-digit hex colour, got {hex_color!r}")
    r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


DASHBOARD_TEMPLATE = go.layout.Template(
    layout=go.Layout(
        font=dict(family=FONT_FAMILY, color=TEXT_COLOR, size=13),
        paper_bgcolor=PAPER_COLOR,
        plot_bgcolor=BG_COLOR,
        colorway=COLORS,
        title=dict(font=dict(size=16, color=TEXT_COLOR)),
        xaxis=dict(
            gridcolor=GRID_COLOR,
            zerolinecolor=GRID_COLOR,
            showgrid=True,
            gridwidth=1,
        ),
        yaxis=dict(
            gridcolor=GRID_COLOR,
            zerolinecolor=GRID_COLOR,
            showgrid=True,
            gridwidth=1,
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=TEXT_COLOR, size=11),
        ),
        hoverlabel=dict(
            bgcolor="#1e293b",
            font_size=12,
            font_color=TEXT_COLOR,
        ),
        margin=dict(l=40, r=20, t=50, b=40),
    )
)

pio.templates["dashboard"] = DASHBOARD_TEMPLATE
pio.templates.default = "dashboard"
