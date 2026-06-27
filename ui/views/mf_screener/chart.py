"""Risk-vs-Return scatter for the MF Screener, with optional Markowitz frontier.
Axes are driven by mutual_funds.metric_catalog."""

from __future__ import annotations

import polars as pl
import streamlit as st

from mutual_funds.metric_catalog import (
    BENCHMARK_DEPENDENT,
    RETURN_AXIS_OPTIONS,
    RISK_AXIS_OPTIONS,
    RISK_FREE_RATE_FOR_EXCESS_RETURN,
)
from services.screener_service import nifty_1y_cagr
from ui.state.loaders import load_metrics_cached, load_screener_df_cached


def render_risk_return_chart(filtered: pl.DataFrame) -> None:
    if filtered.height == 0 or "vol_1y" not in filtered.columns:
        return

    st.divider()
    st.subheader(f"Risk vs Return — filtered universe ({filtered.height:,} funds)")

    cc1, cc2, cc3, cc4, cc5 = st.columns([2, 2, 1, 1, 1])
    risk_choice = cc1.selectbox(
        "X-axis (risk)",
        options=list(RISK_AXIS_OPTIONS.keys()),
        format_func=lambda k: RISK_AXIS_OPTIONS[k][1],
        index=1,  # downside_vol_1y default
        key="screener_chart_x",
    )
    return_choice = cc2.selectbox(
        "Y-axis (return)",
        options=list(RETURN_AXIS_OPTIONS.keys()),
        format_func=lambda k: RETURN_AXIS_OPTIONS[k],
        index=0,
        key="screener_chart_y",
    )
    color_mode = cc3.selectbox(
        "Bubble colour",
        options=("Sharpe", "Sortino", "Category", "Asset class"),
        index=0,
        key="screener_chart_color",
    )
    size_mode = cc4.selectbox(
        "Bubble size",
        options=("AUM", "Uniform"),
        index=0,
        key="screener_chart_size",
        help="AUM scales bubble area by fund size; Uniform makes all equal.",
    )
    show_frontier = cc5.checkbox("Show frontier", value=True, key="screener_chart_frontier")

    risk_db_col, risk_label, risk_take_abs = RISK_AXIS_OPTIONS[risk_choice]
    chart_pdf = filtered.to_pandas()

    # A freshly-added metric column won't be on the cached frame yet — bust caches and rerun
    # once (session-state guard avoids ping-ponging).
    if risk_db_col not in chart_pdf.columns and not st.session_state.get("_screener_cache_busted"):
        st.session_state["_screener_cache_busted"] = True
        load_screener_df_cached.clear()
        load_metrics_cached.clear()
        st.toast(f"Refreshing screener cache to pick up the new `{risk_db_col}` column…", icon="🔄")
        st.rerun()
    if risk_db_col not in chart_pdf.columns:
        st.error(
            f"Column `{risk_db_col}` is still missing after a cache refresh. The metric "
            "isn't populated in `mf_scheme_metrics` yet — run `uv run python "
            "scripts/compute_metrics.py --all` to backfill it."
        )
        st.stop()

    # Some Y axes are derivations (excess return, IR numerator), not direct cache reads.
    return_label, needed_y = _resolve_return_axis(chart_pdf, return_choice)

    chart_pdf = chart_pdf.dropna(subset=[risk_db_col, needed_y, "__return__"])
    if chart_pdf.empty:
        st.info(
            "No funds in the filtered set have both axes populated. Try different axes or "
            "widen the sidebar filters to include funds with NAV history."
        )
        return

    chart_pdf["__risk__"] = chart_pdf[risk_db_col].abs() if risk_take_abs else chart_pdf[risk_db_col]

    # Stored fractions are decimals — scale to percent for the axes.
    chart_pdf["__risk__"] *= 100
    risk_label += " (%)"
    chart_pdf["__return__"] *= 100
    return_label += " (%)"

    _draw_scatter(chart_pdf, risk_label, return_label, color_mode, size_mode, show_frontier)
    _render_captions(risk_db_col, return_choice, size_mode)


def _resolve_return_axis(chart_pdf, return_choice: str) -> tuple[str, str]:
    """Set chart_pdf['__return__'] from the chosen Y axis. Returns (label, source_col)."""
    if return_choice == "excess_return_1y":
        if "cagr_1y" not in chart_pdf.columns:
            st.info("Need cached `cagr_1y` for this axis — recompute metrics first.")
            st.stop()
        chart_pdf["__return__"] = chart_pdf["cagr_1y"] - RISK_FREE_RATE_FOR_EXCESS_RETURN
        label = f"{RETURN_AXIS_OPTIONS[return_choice]} (RF = {RISK_FREE_RATE_FOR_EXCESS_RETURN * 100:.1f}%)"
        return label, "cagr_1y"
    if return_choice == "alpha_1y":
        chart_pdf["__return__"] = chart_pdf.get("alpha_1y")
        return RETURN_AXIS_OPTIONS[return_choice], "alpha_1y"
    if return_choice == "ir_numerator_1y":
        nifty_cagr = nifty_1y_cagr()
        if nifty_cagr is None:
            st.warning(
                "Couldn't load Nifty 50 history to compute the IR-numerator axis. Falling back to Jensen's alpha."
            )
            chart_pdf["__return__"] = chart_pdf.get("alpha_1y")
            return "Jensen's Alpha (Nifty fallback)", "alpha_1y"
        chart_pdf["__return__"] = chart_pdf["cagr_1y"] - nifty_cagr
        label = f"{RETURN_AXIS_OPTIONS[return_choice]} — Nifty 50 1Y CAGR = {nifty_cagr * 100:.2f}%"
        return label, "cagr_1y"
    chart_pdf["__return__"] = chart_pdf.get(return_choice)
    return RETURN_AXIS_OPTIONS[return_choice], return_choice


def _draw_scatter(
    chart_pdf, risk_label: str, return_label: str, color_mode: str, size_mode: str, show_frontier: bool
) -> None:
    """Render the Plotly scatter with optional efficient-frontier overlay."""
    import numpy as np  # noqa: PLC0415 — heavy viz deps; deferred to chart-render time
    import plotly.express as px  # noqa: PLC0415 — heavy viz deps; deferred to chart-render time

    # Bubble size: AUM floored + log-scaled so micro-funds stay visible, or uniform.
    if size_mode == "AUM" and "aum_crores" in chart_pdf.columns:
        aum = chart_pdf["aum_crores"].fillna(0).clip(lower=10)  # floor at ₹10 Cr
        chart_pdf["__size__"] = np.log10(aum + 1) * 6 + 6
    else:
        chart_pdf["__size__"] = 12

    # Sharpe / Sortino are continuous; Category / Asset class are categorical.
    color_kwargs: dict = {}
    if color_mode == "Sharpe" and "sharpe_1y" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["sharpe_1y"]
        color_kwargs.update(
            color="__color__",
            color_continuous_scale="RdYlGn",
            color_continuous_midpoint=0,
            labels={"__color__": "Sharpe"},
        )
    elif color_mode == "Sortino" and "sortino_1y" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["sortino_1y"]
        color_kwargs.update(
            color="__color__",
            color_continuous_scale="RdYlGn",
            color_continuous_midpoint=0,
            labels={"__color__": "Sortino"},
        )
    elif color_mode == "Category" and "category" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["category"].fillna("(uncategorised)")
        color_kwargs.update(color="__color__", labels={"__color__": "Category"})
    elif color_mode == "Asset class" and "asset_class" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["asset_class"].fillna("(unknown)")
        color_kwargs.update(color="__color__", labels={"__color__": "Asset class"})

    hover_cols = [
        c
        for c in (
            "scheme_name",
            "fund_house",
            "category",
            "aum_crores",
            "sharpe_1y",
            "sortino_1y",
            "alpha_1y",
            "beta_1y",
        )
        if c in chart_pdf.columns
    ]

    fig = px.scatter(
        chart_pdf,
        x="__risk__",
        y="__return__",
        size="__size__",
        size_max=32,
        hover_data=hover_cols,
        **color_kwargs,
    )
    fig.update_layout(
        xaxis_title=risk_label,
        yaxis_title=return_label,
        height=560,
        template="plotly_dark",
        margin={"l": 60, "r": 20, "t": 40, "b": 50},
    )

    # Zero reference lines.
    fig.add_hline(y=0, line_color="#64748b", line_dash="dot", line_width=1)
    fig.add_vline(x=0, line_color="#64748b", line_dash="dot", line_width=1)

    if show_frontier:
        _add_efficient_frontier(fig, chart_pdf)

    st.plotly_chart(fig, use_container_width=True, key="screener-rvr")


def _add_efficient_frontier(fig, chart_pdf) -> None:
    """Pareto frontier: walk left-to-right, connecting points that beat the running max-Y."""
    import plotly.graph_objects as go  # noqa: PLC0415 — heavy viz dep; deferred to chart-render time

    sorted_pts = chart_pdf.sort_values("__risk__").reset_index(drop=True)
    front_x: list[float] = []
    front_y: list[float] = []
    front_names: list[str] = []
    running_max = -float("inf")
    for _, r in sorted_pts.iterrows():
        if r["__return__"] > running_max:
            running_max = r["__return__"]
            front_x.append(r["__risk__"])
            front_y.append(r["__return__"])
            front_names.append(r.get("scheme_name", ""))
    if len(front_x) < 2:
        return
    fig.add_trace(
        go.Scatter(
            x=front_x,
            y=front_y,
            mode="lines+markers",
            line={"color": "#fbbf24", "width": 2, "dash": "dash"},
            marker={
                "color": "#fbbf24",
                "size": 12,
                "symbol": "diamond",
                "line": {"color": "#1e293b", "width": 1},
            },
            name="Efficient frontier",
            hovertext=front_names,
            hovertemplate="<b>%{hovertext}</b><br>(risk=%{x:.2f}, return=%{y:.2f})<extra></extra>",
        )
    )


def _render_captions(risk_db_col: str, return_choice: str, size_mode: str) -> None:
    """Footer captions: benchmark caveat (when applicable) + bubble-size / frontier note."""
    if return_choice in BENCHMARK_DEPENDENT or risk_db_col in BENCHMARK_DEPENDENT:
        st.caption(
            "Alpha / Beta / Tracking Error use each fund's category benchmark; "
            "debt / arbitrage / index funds have none, so their CAPM stats are blank."
        )
    size_note = "Bubble size = AUM. " if size_mode == "AUM" else "Bubble size is uniform. "
    st.caption(size_note + "The dashed gold line is the efficient frontier for the filtered set.")
