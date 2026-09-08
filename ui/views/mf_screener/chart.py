"""Risk-vs-Return scatter for the MF Screener, with optional Markowitz frontier.
Axes are driven by mutual_funds.metric_catalog."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    import pandas as pd


def render_risk_return_chart(filtered: pl.DataFrame) -> None:
    if filtered.height == 0 or "vol_1y" not in filtered.columns:
        return

    st.divider()
    st.subheader(f"Risk vs Return — filtered universe ({filtered.height:,} funds)")

    cc1, cc2, cc3, cc4, cc5, cc6 = st.columns([2, 2, 1, 1, 1, 1])
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
    show_holdings = cc6.checkbox(
        "My funds",
        value=True,
        key="screener_chart_holdings",
        help="Overlay the funds you hold (gold diamonds) and your portfolio's net point (star).",
    )

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

    overlay_pdf, portfolio_point = (
        _holdings_overlay(risk_db_col, risk_take_abs, return_choice) if show_holdings else (None, None)
    )

    _draw_scatter(
        chart_pdf, risk_label, return_label, color_mode, size_mode, show_frontier, overlay_pdf, portfolio_point
    )
    _render_captions(risk_db_col, return_choice, size_mode)
    if show_holdings and overlay_pdf is None:
        st.caption("Upload a tradebook in Settings to overlay your held funds and portfolio point.")


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


def _apply_return_axis(pdf: pd.DataFrame, return_choice: str) -> None:
    """Pure counterpart of _resolve_return_axis for overlay frames (no widgets/toasts)."""
    if return_choice == "excess_return_1y":
        pdf["__return__"] = pdf["cagr_1y"] - RISK_FREE_RATE_FOR_EXCESS_RETURN
    elif return_choice == "ir_numerator_1y":
        nifty_cagr = nifty_1y_cagr()
        pdf["__return__"] = pdf["cagr_1y"] - nifty_cagr if nifty_cagr is not None else pdf.get("alpha_1y")
    else:
        pdf["__return__"] = pdf.get(return_choice)


def _holdings_overlay(
    risk_db_col: str, risk_take_abs: bool, return_choice: str
) -> tuple[pd.DataFrame | None, dict | None]:
    """Held funds on the current axes + the portfolio's aggregate point.

    Held funds come from the FULL screener frame so sidebar filters can't hide them.
    Returns (overlay frame with __risk__/__return__ in %, portfolio highlight dict);
    (None, None) without a tradebook.
    """
    from mutual_funds.display import unique_short_names  # noqa: PLC0415 — deferred with the chart deps
    from services.mf_metrics import portfolio_axis_metrics  # noqa: PLC0415
    from services.portfolio_analytics import fund_values_from_nav  # noqa: PLC0415
    from services.portfolio_service import build_portfolio_returns_series, get_mapped_data  # noqa: PLC0415
    from ui.state.loaders import load_txn_data  # noqa: PLC0415

    txn = load_txn_data()
    result = get_mapped_data(txn) if txn is not None else None
    if result is None:
        return None, None
    mapped, portfolio_nav = result
    held = sorted(fund_values_from_nav(mapped, portfolio_nav))
    if not held:
        return None, None

    overlay = load_screener_df_cached().filter(pl.col("scheme_name").is_in(held)).to_pandas()
    if overlay.empty or risk_db_col not in overlay.columns:
        overlay_pdf = None
    else:
        _apply_return_axis(overlay, return_choice)
        overlay = overlay.dropna(subset=[risk_db_col, "__return__"])
        overlay["__risk__"] = (overlay[risk_db_col].abs() if risk_take_abs else overlay[risk_db_col]) * 100
        overlay["__return__"] = overlay["__return__"] * 100
        overlay["__label__"] = overlay["scheme_name"].map(unique_short_names(overlay["scheme_name"]))
        overlay_pdf = overlay if not overlay.empty else None

    # Portfolio net point from the time-weighted returns series, on the same axes.
    portfolio_point = None
    pf_returns = build_portfolio_returns_series(mapped, portfolio_nav)
    metrics = portfolio_axis_metrics(pf_returns)
    x = metrics.get(risk_db_col)
    y = _portfolio_return_value(metrics, return_choice, pf_returns)
    if x is not None and y is not None and not math.isnan(x) and not math.isnan(y):
        portfolio_point = {"x": abs(x) * 100 if risk_take_abs else x * 100, "y": y * 100, "name": "Portfolio"}
    return overlay_pdf, portfolio_point


def _portfolio_return_value(metrics: dict, return_choice: str, pf_returns) -> float | None:
    """Portfolio Y-axis value; alpha is computed vs Nifty 50 (a portfolio has no category)."""
    cagr = metrics.get("cagr_1y")
    if return_choice == "excess_return_1y":
        return cagr - RISK_FREE_RATE_FOR_EXCESS_RETURN if cagr is not None else None
    if return_choice == "ir_numerator_1y":
        nifty_cagr = nifty_1y_cagr()
        return cagr - nifty_cagr if cagr is not None and nifty_cagr is not None else None
    if return_choice == "alpha_1y":
        from services.mf_metrics import compute_alpha_beta  # noqa: PLC0415
        from ui.state.loaders import load_nifty_returns  # noqa: PLC0415

        if pf_returns.empty:
            return None
        try:
            nifty = load_nifty_returns(pf_returns.index.min().to_pydatetime(), pf_returns.index.max().to_pydatetime())
        except Exception:
            return None
        ab = compute_alpha_beta(pf_returns.iloc[-252:], nifty)
        return ab["alpha"] if ab is not None else None
    return None


def _draw_scatter(
    chart_pdf,
    risk_label: str,
    return_label: str,
    color_mode: str,
    size_mode: str,
    show_frontier: bool,
    overlay_pdf: pd.DataFrame | None = None,
    portfolio_point: dict | None = None,
) -> None:
    """Prepare size/colour columns and delegate to the shared risk/return scatter."""
    import numpy as np  # noqa: PLC0415 — heavy viz deps; deferred to chart-render time

    from ui.charts.risk_return_scatter import render_scatter  # noqa: PLC0415 — heavy viz deps

    # Bubble size: AUM floored + log-scaled so micro-funds stay visible, or uniform.
    if size_mode == "AUM" and "aum_crores" in chart_pdf.columns:
        aum = chart_pdf["aum_crores"].fillna(0).clip(lower=10)  # floor at ₹10 Cr
        chart_pdf["__size__"] = np.log10(aum + 1) * 6 + 6
    else:
        chart_pdf["__size__"] = 12

    # Sharpe / Sortino are continuous; Category / Asset class are categorical.
    color_continuous = False
    color_label = None
    if color_mode == "Sharpe" and "sharpe_1y" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["sharpe_1y"]
        color_continuous, color_label = True, "Sharpe"
    elif color_mode == "Sortino" and "sortino_1y" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["sortino_1y"]
        color_continuous, color_label = True, "Sortino"
    elif color_mode == "Category" and "category" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["category"].fillna("(uncategorised)")
        color_label = "Category"
    elif color_mode == "Asset class" and "asset_class" in chart_pdf.columns:
        chart_pdf["__color__"] = chart_pdf["asset_class"].fillna("(unknown)")
        color_label = "Asset class"

    # Robust colourbar for the continuous modes: a lone outlier Sharpe/Sortino used to
    # stretch the scale until every other fund looked mid-yellow. Clamp to the 5th–95th
    # percentile (always spanning 0 so negative stays red); outliers clip to the end colours.
    color_range = None
    if color_continuous:
        vals = chart_pdf["__color__"].dropna()
        if len(vals) >= 10:
            lo, hi = float(vals.quantile(0.05)), float(vals.quantile(0.95))
            if hi > lo:
                color_range = (min(lo, 0.0), max(hi, 0.1))

    render_scatter(
        chart_pdf,
        x_col="__risk__",
        y_col="__return__",
        x_title=risk_label,
        y_title=return_label,
        key="screener-rvr",
        size_col="__size__",
        color_col="__color__" if "__color__" in chart_pdf.columns else None,
        color_continuous=color_continuous,
        color_range=color_range,
        color_label=color_label,
        hover_cols=(
            "scheme_name",
            "fund_house",
            "category",
            "aum_crores",
            "sharpe_1y",
            "sortino_1y",
            "alpha_1y",
            "beta_1y",
        ),
        frontier=show_frontier,
        frontier_name_col="scheme_name",
        overlay_pdf=overlay_pdf,
        overlay_label_col="__label__",
        overlay_name="My funds",
        highlight=portfolio_point,
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
