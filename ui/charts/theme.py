"""Consistent Plotly theme for the financial dashboard."""

import plotly.graph_objects as go
import plotly.io as pio

# Color palette
COLORS = [
    "#6366f1",  # indigo
    "#f59e0b",  # amber
    "#10b981",  # emerald
    "#ef4444",  # red
    "#8b5cf6",  # violet
    "#06b6d4",  # cyan
    "#f97316",  # orange
    "#ec4899",  # pink
]

BG_COLOR = "#0f1117"
PAPER_COLOR = "#0f1117"
GRID_COLOR = "#1e293b"
TEXT_COLOR = "#e2e8f0"
FONT_FAMILY = "Inter, system-ui, sans-serif"

# Semantic colours — import these instead of hard-coding hex in views/charts.
POSITIVE = "#10b981"  # emerald — gains, good deltas
POSITIVE_SOFT = "#86efac"  # light green — raw NAV / price lines
NEGATIVE = "#ef4444"  # red — losses, bad deltas
NEGATIVE_SOFT = "#fca5a5"  # light red — drawdown fills
NEUTRAL = "#64748b"  # slate — zero/reference lines
QUADRANT_LINE = "#475569"  # darker slate — quadrant split lines
ACCENT = "#6366f1"  # indigo — primary series
INFO = "#60a5fa"  # blue — secondary series (rolling returns etc.)
BENCHMARK_LINE = "#94a3b8"  # grey — benchmark index overlays
WARNING = "#fbbf24"  # amber — highlights, frontier, portfolio marker
LINK = "#5aa9ff"  # clickable cell/link styling

# Tight margins for dense views (e.g. MF Analysis) where charts sit under an st.subheader
# (no Plotly title) — trims the default whitespace so more fits on screen without scrolling.
COMPACT_MARGIN = {"l": 48, "r": 16, "t": 24, "b": 36}

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

# Register as the default template
pio.templates["dashboard"] = DASHBOARD_TEMPLATE
pio.templates.default = "dashboard"
