"""Stock Analysis · Fundamentals tab — momentum, valuation percentiles, quarterly trend.

Data: stock_ohlcv (momentum) + screener.in snapshots (stock_metrics / stock_quarterly).
Percentiles are vs the screened universe — the data has no sector classification.
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from services.stock_fundamentals_service import fundamentals_percentiles, momentum_stats, quarterly_trend
from stocks.constants import to_bare_symbol
from ui.charts import theme
from ui.components.metric_tiles import Kpi, fmt_pct, render_kpi_row
from ui.state.loaders import load_stock_screener_df_cached


def render(symbol: str) -> None:
    bare = to_bare_symbol(symbol)
    _render_momentum(symbol)
    st.divider()
    _render_valuation(bare)
    st.divider()
    _render_quarterly(bare)


def _render_momentum(symbol: str) -> None:
    st.subheader("Momentum")
    m = momentum_stats(symbol)
    if m is None:
        st.info("No price history cached for this symbol.")
        return
    pos = m["pos_52w"]
    render_kpi_row(
        [
            Kpi("Last close", f"₹{m['last_close']:,.1f}", help=f"As of {m['as_of']:%d %b %Y}"),
            Kpi(
                "52w range position",
                f"{pos * 100:.0f}%" if pos is not None else None,
                help=f"52w low ₹{m['low_52w']:,.0f} · high ₹{m['high_52w']:,.0f}",
            ),
            Kpi("From 52w high", m["dist_from_high"], fmt="pct"),
            Kpi("vs 50-DMA", m["vs_dma_50"], fmt="pct"),
            Kpi("vs 200-DMA", m["vs_dma_200"], fmt="pct"),
        ]
    )
    render_kpi_row(
        [
            Kpi("1M", m["ret_1m"], fmt="pct"),
            Kpi("3M", m["ret_3m"], fmt="pct"),
            Kpi("6M", m["ret_6m"], fmt="pct"),
            Kpi("1Y", m["ret_1y"], fmt="pct"),
        ]
    )


def _render_valuation(bare: str) -> None:
    st.subheader("Valuation & quality")
    universe = load_stock_screener_df_cached()
    if universe.is_empty() or bare not in universe["symbol"].to_list():
        st.info("No screener.in fundamentals cached for this symbol yet.")
        if st.button("Sync fundamentals from screener.in", key="sync-fundamentals"):
            from services.stock_sync_service import sync_stocks  # noqa: PLC0415 — defer heavy import off boot

            with st.spinner(f"Scraping screener.in for {bare}…"):
                sync_stocks([bare])
            load_stock_screener_df_cached.clear()
            st.rerun()
        return

    row = universe.filter(pl.col("symbol") == bare).row(0, named=True)
    pct = fundamentals_percentiles(universe)
    prow = pct.filter(pl.col("symbol") == bare).row(0, named=True) if pct.height else {}

    def _p(col: str) -> str | None:
        v = prow.get(f"{col}_pctile")
        return f"P{v:.0f} in universe" if v is not None else None

    render_kpi_row(
        [
            Kpi("Market cap (Cr)", row.get("market_cap"), fmt="inr"),
            Kpi("P/E", row.get("stock_pe"), fmt="ratio", delta=_p("stock_pe"), help="Percentile favours cheaper P/E."),
            Kpi("ROE %", row.get("roe"), fmt="ratio", delta=_p("roe")),
            Kpi("ROCE %", row.get("roce"), fmt="ratio", delta=_p("roce")),
        ]
    )
    render_kpi_row(
        [
            Kpi(
                "YoY sales growth",
                f"{row['yoy_sales_growth']:+.1f}%" if row.get("yoy_sales_growth") is not None else None,
                delta=_p("yoy_sales_growth"),
            ),
            Kpi(
                "YoY profit growth",
                f"{row['yoy_profit_growth']:+.1f}%" if row.get("yoy_profit_growth") is not None else None,
                delta=_p("yoy_profit_growth"),
            ),
            Kpi("Dividend yield %", row.get("dividend_yield"), fmt="ratio", delta=_p("dividend_yield")),
            Kpi(
                "Promoter %",
                row.get("promoter_holding"),
                fmt="ratio",
                delta=f"{row['promoter_holding_change_1y']:+.1f} pp / 1y"
                if row.get("promoter_holding_change_1y") is not None
                else None,
                help="Delta = promoter-holding change over the last year.",
            ),
        ]
    )
    st.caption(
        f"Percentile badges rank **{bare}** within the {universe.height}-stock screened universe "
        "(no sector data available) — P100 is best on that metric's lens."
    )


def _render_quarterly(bare: str) -> None:
    st.subheader("Quarterly results (screener.in)")
    q = quarterly_trend(bare)
    if q.is_empty():
        st.caption("No quarterly history cached for this symbol.")
        return

    import plotly.graph_objects as go  # noqa: PLC0415 — heavy viz dep; deferred to render time
    from plotly.subplots import make_subplots  # noqa: PLC0415 — heavy viz dep

    pdf = q.to_pandas()
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(x=pdf["period_label"], y=pdf["sales"], name="Sales (Cr)", marker_color=theme.ACCENT, opacity=0.75)
    )
    fig.add_trace(
        go.Bar(
            x=pdf["period_label"], y=pdf["net_profit"], name="Net profit (Cr)", marker_color=theme.POSITIVE, opacity=0.9
        )
    )
    fig.add_trace(
        go.Scatter(
            x=pdf["period_label"],
            y=pdf["opm_pct"],
            name="OPM %",
            mode="lines+markers",
            line={"color": theme.WARNING, "width": 2},
        ),
        secondary_y=True,
    )
    fig.update_layout(height=380, barmode="group", legend={"orientation": "h", "y": 1.08})
    fig.update_yaxes(title_text="₹ Cr", secondary_y=False)
    fig.update_yaxes(title_text="OPM %", secondary_y=True, showgrid=False)
    st.plotly_chart(fig, use_container_width=True, key="stock-quarterly")

    latest, prev = pdf.iloc[-1], (pdf.iloc[-5] if len(pdf) >= 5 else None)
    yoy_sales = (latest["sales"] / prev["sales"] - 1) if prev is not None and prev["sales"] else None
    yoy_profit = (latest["net_profit"] / prev["net_profit"] - 1) if prev is not None and prev["net_profit"] else None
    render_kpi_row(
        [
            Kpi(f"Sales {latest['period_label']}", latest["sales"], fmt="inr"),
            Kpi("Net profit", latest["net_profit"], fmt="inr"),
            Kpi("OPM %", latest["opm_pct"], fmt="ratio"),
            Kpi("Sales YoY", fmt_pct(yoy_sales) if yoy_sales is not None else None),
            Kpi("Profit YoY", fmt_pct(yoy_profit) if yoy_profit is not None else None),
        ]
    )
