"""Mutual Fund Analysis — single-fund deep dive."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import quantstats as qs
import streamlit as st

from data.repositories.amfi import get_scheme_details_by_name, load_amfi_df
from data.repositories.holdings import load_assets, load_holdings, load_sectors
from data.repositories.metadata import load_metadata
from data.repositories.nav import load_nav_df
from data.repositories.scheme_metrics import load_metrics
from mutual_funds.display import detect_option, detect_plan, make_slug, short_scheme_name
from mutual_funds.holdings_stats import quick_stats
from services.benchmarks import resolve_benchmark_symbol
from services.mf_metrics import absolute_return, compute_tracking_error, windowed_cagr
from services.registry_service import backfill_missing, list_tracked
from services.screener_service import apply_name_filter
from ui.charts import theme
from ui.components.freshness_banner import render_freshness_banner
from ui.components.metric_tiles import Kpi, render_kpi_row
from ui.components.mutual_fund_holdings import render_holdings_table
from ui.constants import RF_DAILY
from ui.state.filter_persistence import cascade_subcategory_options, hydrate_filters, make_persist_callback
from ui.state.loaders import load_benchmark_returns

# Filter + selected-fund state persisted to selections.json so it survives page nav/refresh.
_MF_PERSIST_KEY = "mf_analysis_filters"
_MF_FILTER_DEFAULTS = {
    "mf_analysis_search": "",
    "mf_analysis_amc": [],
    "mf_analysis_cat": [],
    "mf_analysis_sub_cat": [],
    "mf_analysis_plan": [],
    "mf_analysis_option": [],
    "mf_analysis_only_meta": False,
    "mf_analysis_only_holdings": False,
    "mf_analysis_min_age": 0.0,
    "mf_analysis_fund": None,
}
_persist_mf_filters = make_persist_callback(_MF_PERSIST_KEY, _MF_FILTER_DEFAULTS)


@st.cache_data(ttl=600, show_spinner=False)
def _load_tracked_enriched(names: tuple[str, ...]) -> pl.DataFrame:
    """Tracked funds + AMFI category/sub-category + computed plan/option, for the filters."""
    empty_schema = {
        "scheme_name": pl.Utf8,
        "fund_house": pl.Utf8,
        "category": pl.Utf8,
        "sub_category": pl.Utf8,
        "plan": pl.Utf8,
        "option": pl.Utf8,
        "inception_date": pl.Date,
    }
    if not names:
        return pl.DataFrame(schema=empty_schema)

    # inception_date (first NAV date) from cached metrics — the age filter's anchor.
    _m = load_metrics(list(names))
    inc = (
        _m.select(["scheme_name", "inception_date"])
        if "inception_date" in _m.columns
        else pl.DataFrame(schema={"scheme_name": pl.Utf8, "inception_date": pl.Date})
    )

    amfi = load_amfi_df().filter(pl.col("scheme_name").is_in(list(names)))
    if amfi.is_empty():
        # Fallback: scheme_name + computed plan/option so search still works.
        return (
            pl.DataFrame({"scheme_name": list(names)})
            .with_columns(
                pl.lit(None, dtype=pl.Utf8).alias("fund_house"),
                pl.lit(None, dtype=pl.Utf8).alias("category"),
                pl.lit(None, dtype=pl.Utf8).alias("sub_category"),
                pl.col("scheme_name").map_elements(detect_plan, return_dtype=pl.Utf8).alias("plan"),
                pl.col("scheme_name").map_elements(detect_option, return_dtype=pl.Utf8).alias("option"),
            )
            .join(inc, on="scheme_name", how="left")
        )

    enriched = amfi.with_columns(
        pl.col("scheme_name").map_elements(detect_plan, return_dtype=pl.Utf8).alias("plan"),
        pl.col("scheme_name").map_elements(detect_option, return_dtype=pl.Utf8).alias("option"),
    ).join(inc, on="scheme_name", how="left")
    return enriched.select(
        ["scheme_name", "fund_house", "category", "sub_category", "plan", "option", "inception_date"]
    )


def _load_index_returns(symbol: str, start: datetime, end: datetime) -> pd.Series:
    """Daily pct-change series via the shared cached loader; empty Series on failure."""
    try:
        return load_benchmark_returns(symbol, start, end)
    except Exception:
        return pd.Series(dtype="float64")


def _rebased_index(returns: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Cumprod of daily returns reindexed to `dates`, rebased to 100 at the first non-null."""
    if returns.empty:
        return pd.Series(dtype="float64")
    idx = (1 + returns).cumprod()
    idx = idx.reindex(dates).ffill().bfill()
    if idx.dropna().empty:
        return pd.Series(dtype="float64")
    first = idx.dropna().iloc[0]
    return idx / first * 100


st.title("Mutual Fund Analysis")

# ---- Fund selection (sidebar filters narrow the dropdown)
tracked = list_tracked()
if tracked.is_empty():
    st.info("No tracked funds yet. Add funds from the **MF Screener** page.")
    st.stop()

all_tracked_names = tracked["schemeName"].to_list()
enriched = _load_tracked_enriched(tuple(all_tracked_names))

hydrate_filters(_MF_PERSIST_KEY, _MF_FILTER_DEFAULTS)  # seed from disk before any filter widget is built

with st.sidebar:
    st.header("Filter funds")
    name_query = st.text_input(
        "Search by name",
        placeholder="e.g. parag parikh flexi",
        key="mf_analysis_search",
        on_change=_persist_mf_filters,
        help="Case-insensitive substring match; multiple words are ANDed.",
    )
    amc_options = sorted(enriched["fund_house"].drop_nulls().unique().to_list())
    cat_options = sorted(enriched["category"].drop_nulls().unique().to_list())
    # Sub-category cascades off the selected category; stale persisted values pruned.
    sub_cat_options = cascade_subcategory_options(
        enriched, cat_key="mf_analysis_cat", sub_cat_key="mf_analysis_sub_cat"
    )

    amc_filter = st.multiselect("AMC", amc_options, key="mf_analysis_amc", on_change=_persist_mf_filters)
    cat_filter = st.multiselect("Category", cat_options, key="mf_analysis_cat", on_change=_persist_mf_filters)
    sub_cat_filter = st.multiselect(
        "Sub-category", sub_cat_options, key="mf_analysis_sub_cat", on_change=_persist_mf_filters
    )
    plan_filter = st.multiselect("Plan", ["Direct", "Regular"], key="mf_analysis_plan", on_change=_persist_mf_filters)
    option_filter = st.multiselect(
        "Option", ["Growth", "IDCW", "Bonus", "ETF", "Other"], key="mf_analysis_option", on_change=_persist_mf_filters
    )
    min_age_years = st.slider(
        "Min fund age (years)",
        0.0,
        25.0,
        step=0.5,
        key="mf_analysis_min_age",
        on_change=_persist_mf_filters,
        help="Keep funds with at least this much NAV history.",
    )

    st.divider()
    st.caption("Data availability")
    only_with_metadata = st.checkbox("Only with metadata", key="mf_analysis_only_meta", on_change=_persist_mf_filters)
    only_with_holdings = st.checkbox(
        "Only with holdings", key="mf_analysis_only_holdings", on_change=_persist_mf_filters
    )

filtered = apply_name_filter(enriched, name_query)
if amc_filter:
    filtered = filtered.filter(pl.col("fund_house").is_in(amc_filter))
if cat_filter:
    filtered = filtered.filter(pl.col("category").is_in(cat_filter))
if sub_cat_filter:
    filtered = filtered.filter(pl.col("sub_category").is_in(sub_cat_filter))
if plan_filter:
    filtered = filtered.filter(pl.col("plan").is_in(plan_filter))
if option_filter:
    filtered = filtered.filter(pl.col("option").is_in(option_filter))
if min_age_years > 0 and "inception_date" in filtered.columns:
    _cutoff = date.today() - timedelta(days=round(min_age_years * 365.25))
    filtered = filtered.filter(pl.col("inception_date") <= _cutoff)

# Availability filters join against the tracked source statuses.
if only_with_metadata or only_with_holdings:
    status_df = tracked.select(
        pl.col("schemeName").alias("scheme_name"),
        pl.col("metadataStatus"),
        pl.col("holdingsStatus"),
    )
    filtered = filtered.join(status_df, on="scheme_name", how="left")
    if only_with_metadata:
        filtered = filtered.filter(pl.col("metadataStatus") == "available")
    if only_with_holdings:
        filtered = filtered.filter(pl.col("holdingsStatus") == "available")
    filtered = filtered.drop("metadataStatus", "holdingsStatus")

scheme_names = sorted(filtered["scheme_name"].to_list(), key=lambda n: short_scheme_name(n).lower())

if not scheme_names:
    st.info(
        f"No tracked funds match the current filters ({len(all_tracked_names):,} tracked total). "
        "Clear filters in the sidebar or add more funds via **MF Screener**."
    )
    st.stop()

st.caption(f"Showing **{len(scheme_names):,}** of {len(all_tracked_names):,} tracked funds")

# Drop a persisted fund the filters now exclude, so the selectbox value stays in its options.
if st.session_state.get("mf_analysis_fund") not in scheme_names:
    st.session_state.pop("mf_analysis_fund", None)

selected = st.selectbox(
    "Fund",
    options=scheme_names,
    format_func=short_scheme_name,
    key="mf_analysis_fund",
    on_change=_persist_mf_filters,
)

# ---- Auto-fetch any pending sources for this fund before rendering
reg_row = tracked.filter(pl.col("schemeName") == selected).row(0, named=True)
nav_status = reg_row["navStatus"]
holdings_status = reg_row["holdingsStatus"]
metadata_status = reg_row["metadataStatus"]

pending_sources = [
    s
    for s, status in [("nav", nav_status), ("metadata", metadata_status), ("holdings", holdings_status)]
    if status == "pending"
]

if pending_sources:
    with st.spinner(f"Fetching {' + '.join(pending_sources)} for **{short_scheme_name(selected)}**…"):
        backfill_missing(
            scheme_names=[selected],
            sources=tuple(pending_sources),
            max_per_run=len(pending_sources),
            submit_delay=0.0,  # single fund, no rate limiting needed
        )
    st.rerun()

# ---- Banners for sources confirmed unavailable
if nav_status == "unavailable":
    st.error(f"NAV data is **unavailable** for *{short_scheme_name(selected)}*. Try **Settings → Retry unavailable**.")
    st.stop()
if metadata_status == "unavailable":
    st.warning("Metadata not available for this fund — header AMC/AUM/TER/benchmark fields will be partial.")

# ---- Data-freshness banner for the selected fund
render_freshness_banner([selected], [make_slug(selected)])

# ---- Header — AMFI + metadata at a glance
amfi_row = get_scheme_details_by_name(selected)

meta_df = load_metadata([selected])
meta = meta_df.row(0, named=True) if meta_df.height else {}

st.markdown(f"### {short_scheme_name(selected)}")
st.caption(selected)

amc = (meta.get("fundHouse") or (amfi_row["fund_house"] if amfi_row else None)) or "—"
category = (meta.get("category") or (amfi_row["category"] if amfi_row else None)) or "—"
aum = meta.get("aumCrores")
ter = meta.get("expenseRatio")
benchmark = meta.get("benchmark") or "—"
launch = meta.get("launchDate")

render_kpi_row(
    [
        Kpi("AMC", amc),
        Kpi("Category", category),
        Kpi("AUM (₹ Cr)", aum or None, fmt="inr"),
        Kpi("TER %", ter or None, fmt="ratio"),
    ]
)
render_kpi_row(
    [
        Kpi("Plan", detect_plan(selected) or "—"),
        Kpi("Option", detect_option(selected)),
        Kpi("Benchmark", benchmark),
        Kpi("Launched", str(launch) if launch else None),
    ]
)


# ---- Load NAV
nav_df = load_nav_df([selected]).sort("date")
if nav_df.is_empty():
    st.warning("No NAV history for this fund.")
    st.stop()

nav_pd = nav_df.to_pandas().set_index("date")["nav"].astype(float).sort_index()
returns = nav_pd.pct_change().dropna()

# ---- Tabs
tab_growth, tab_risk, tab_holdings, tab_calendar, tab_about = st.tabs(
    ["NAV & Returns", "Risk", "Holdings", "Calendar Returns", "About"]
)

# ===== NAV & Returns =====
with tab_growth:
    render_kpi_row(
        [
            Kpi("1M", absolute_return(nav_pd, 21), fmt="pct"),
            Kpi("3M", absolute_return(nav_pd, 63), fmt="pct"),
            Kpi("6M", absolute_return(nav_pd, 126), fmt="pct"),
            Kpi("1Y", absolute_return(nav_pd, 252), fmt="pct"),
            Kpi("3Y CAGR", windowed_cagr(nav_pd, 252 * 3), fmt="pct"),
            Kpi("5Y CAGR", windowed_cagr(nav_pd, 252 * 5), fmt="pct"),
        ]
    )

    st.subheader("NAV growth (rebased to 100, vs benchmarks)")

    # Comparison index: fund + Nifty 50 (always) + actual benchmark (if resolvable).
    fund_dates = pd.DatetimeIndex(pd.to_datetime(nav_pd.index))
    start_dt = fund_dates.min().to_pydatetime()
    end_dt = fund_dates.max().to_pydatetime()

    fig = go.Figure()
    fund_idx = nav_pd / nav_pd.iloc[0] * 100
    fig.add_trace(
        go.Scatter(
            x=fund_idx.index,
            y=fund_idx.values,
            name=short_scheme_name(selected),
            mode="lines",
            line={"width": 2.5},
        )
    )

    nifty_returns = _load_index_returns("^NSEI", start_dt, end_dt)
    nifty_idx = _rebased_index(nifty_returns, fund_dates)
    if not nifty_idx.empty:
        fig.add_trace(
            go.Scatter(
                x=nifty_idx.index,
                y=nifty_idx.values,
                name="Nifty 50",
                mode="lines",
                line={"dash": "dot", "color": theme.BENCHMARK_LINE},
            )
        )
    else:
        st.caption("⚠️ Nifty 50 data could not be loaded for this date range.")

    bench_symbol = resolve_benchmark_symbol(meta.get("benchmark"))
    if bench_symbol and bench_symbol != "^NSEI":
        bench_returns = _load_index_returns(bench_symbol, start_dt, end_dt)
        bench_idx = _rebased_index(bench_returns, fund_dates)
        if not bench_idx.empty:
            fig.add_trace(
                go.Scatter(
                    x=bench_idx.index,
                    y=bench_idx.values,
                    name=meta.get("benchmark") or bench_symbol,
                    mode="lines",
                    line={"dash": "dash", "color": theme.WARNING},
                )
            )
        else:
            st.caption(
                f"⚠️ Benchmark `{meta.get('benchmark')}` resolved to `{bench_symbol}` but no data could be fetched."
            )
    elif meta.get("benchmark") and not bench_symbol:
        st.caption(f"Benchmark `{meta.get('benchmark')}` has no fetchable symbol mapping. Showing Nifty 50 only.")

    fig.update_layout(
        height=480,
        hovermode="x unified",
        yaxis_title="Index (start = 100)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
    )
    st.plotly_chart(fig, use_container_width=True, key="mf-detail-growth")

    # ---- Rolling returns
    st.subheader("Rolling returns")
    rr_windows = {
        "3 Months": 63,
        "6 Months": 126,
        "1 Year": 252,
        "3 Years": 252 * 3,
        "5 Years": 252 * 5,
    }
    available_labels = [label for label, w in rr_windows.items() if len(nav_pd) >= w + 30]
    if not available_labels:
        st.info("Need ≥ ~3 months of NAV history for rolling returns.")
    else:
        win_label = st.radio(
            "Window",
            options=available_labels,
            index=min(2, len(available_labels) - 1),
            horizontal=True,
            key="mf-rolling-window",
        )
        win = rr_windows[win_label]
        rr = nav_pd.pct_change(win, fill_method=None).dropna()
        # Annualise windows ≥ 252 days; otherwise show the period return as-is.
        if win >= 252:
            rr = (1 + rr) ** (252 / win) - 1
            ylabel = f"{win_label} CAGR %"
        else:
            ylabel = f"{win_label} return %"
        rr_pct = rr * 100

        render_kpi_row(
            [
                Kpi("Mean", rr.mean(), fmt="pct"),
                Kpi("Min", rr.min(), fmt="pct"),
                Kpi("Max", rr.max(), fmt="pct"),
                Kpi("Latest", rr.iloc[-1], fmt="pct"),
            ]
        )

        fig_rr = go.Figure()
        fig_rr.add_trace(go.Scatter(x=rr_pct.index, y=rr_pct.values, mode="lines", line={"color": theme.INFO}))
        fig_rr.add_hline(y=0, line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"})
        fig_rr.update_layout(height=320, yaxis_title=ylabel, hovermode="x")
        st.plotly_chart(fig_rr, use_container_width=True, key="mf-detail-rolling")

    st.subheader("NAV (raw)")
    fig_nav = go.Figure(go.Scatter(x=nav_pd.index, y=nav_pd.values, mode="lines", line={"color": theme.POSITIVE_SOFT}))
    fig_nav.update_layout(height=300, yaxis_title="NAV (₹)", hovermode="x")
    st.plotly_chart(fig_nav, use_container_width=True, key="mf-detail-nav")

# ===== Risk =====
with tab_risk:
    if len(returns) < 252:
        st.info(f"Need ≥ 1 year of NAV history for risk metrics — only {len(returns)} days available.")
    else:
        last_year = returns.iloc[-252:]
        last_3y = returns.iloc[-min(252 * 3, len(returns)) :]

        def _q(fn, *a, **kw):
            try:
                return float(fn(*a, **kw))
            except Exception:
                return float("nan")

        sharpe_1y = _q(qs.stats.sharpe, last_year, rf=RF_DAILY)
        sortino_1y = _q(qs.stats.sortino, last_year, rf=RF_DAILY)
        vol_1y = _q(qs.stats.volatility, last_year)
        max_dd_1y = _q(qs.stats.max_drawdown, last_year)
        sharpe_3y = _q(qs.stats.sharpe, last_3y, rf=RF_DAILY)
        sortino_3y = _q(qs.stats.sortino, last_3y, rf=RF_DAILY)
        vol_3y = _q(qs.stats.volatility, last_3y)
        max_dd_all = _q(qs.stats.max_drawdown, returns)

        # Beta vs Nifty (1Y)
        nifty_r = _load_index_returns(
            "^NSEI",
            nav_pd.index.min().to_pydatetime(),
            nav_pd.index.max().to_pydatetime(),
        )
        beta = alpha = r2 = float("nan")
        if not nifty_r.empty:
            common = pd.concat([last_year.rename("fund"), nifty_r.rename("nifty")], axis=1, join="inner").dropna()
            if len(common) >= 60 and common["nifty"].var() > 0:
                cov = common["fund"].cov(common["nifty"])
                var = common["nifty"].var()
                beta = cov / var
                alpha = common["fund"].mean() - beta * common["nifty"].mean()
                alpha = alpha * 252
                r2 = common["fund"].corr(common["nifty"]) ** 2

        st.subheader("1-year metrics")
        render_kpi_row(
            [
                Kpi("Sharpe", sharpe_1y, fmt="ratio"),
                Kpi("Sortino", sortino_1y, fmt="ratio"),
                Kpi("Volatility", vol_1y, fmt="pct_unsigned"),
                Kpi("Max drawdown", max_dd_1y, fmt="pct"),
            ]
        )

        st.subheader("3-year metrics")
        render_kpi_row(
            [
                Kpi("Sharpe", sharpe_3y, fmt="ratio"),
                Kpi("Sortino", sortino_3y, fmt="ratio"),
                Kpi("Volatility", vol_3y, fmt="pct_unsigned"),
                Kpi("Max DD (all-time)", max_dd_all, fmt="pct"),
            ]
        )

        st.subheader("vs Nifty 50 (1Y)")
        # Tracking error vs the fund's benchmark; falls back to Nifty if unmappable.
        bench_returns_for_te = nifty_r
        bench_label_for_te = "Nifty 50"
        bench_sym = resolve_benchmark_symbol(meta.get("benchmark"))
        if bench_sym and bench_sym != "^NSEI":
            br = _load_index_returns(bench_sym, nav_pd.index.min().to_pydatetime(), nav_pd.index.max().to_pydatetime())
            if not br.empty:
                bench_returns_for_te = br
                bench_label_for_te = meta.get("benchmark") or bench_sym
        te = compute_tracking_error(selected, bench_returns_for_te) if not bench_returns_for_te.empty else None

        ath = float(nav_pd.max())
        pct_from_ath = (float(nav_pd.iloc[-1]) / ath - 1) if ath > 0 else float("nan")

        render_kpi_row(
            [
                Kpi("Beta", beta, fmt="ratio"),
                Kpi("Alpha (annualised)", f"{alpha * 100:+.2f}%" if not np.isnan(alpha) else None),
                Kpi("R²", r2, fmt="ratio"),
                Kpi(f"Tracking error vs {bench_label_for_te}", f"{te * 100:.2f}%" if te is not None else None),
                Kpi("% from ATH", f"{pct_from_ath * 100:+.2f}%" if not np.isnan(pct_from_ath) else None),
            ]
        )

        st.subheader("Drawdown")
        cumulative = (1 + returns).cumprod()
        peak = cumulative.cummax()
        drawdown = (cumulative - peak) / peak * 100
        fig_dd = go.Figure(
            go.Scatter(
                x=drawdown.index, y=drawdown.values, fill="tozeroy", mode="lines", line={"color": theme.NEGATIVE_SOFT}
            )
        )
        fig_dd.update_layout(height=320, yaxis_title="Drawdown %", hovermode="x")
        st.plotly_chart(fig_dd, use_container_width=True, key="mf-detail-dd")

        st.subheader("Daily-return distribution")
        fig_dist = px.histogram(returns * 100, nbins=80, marginal="rug")
        fig_dist.update_layout(showlegend=False, height=320, xaxis_title="Daily return %")
        st.plotly_chart(fig_dist, use_container_width=True, key="mf-detail-dist")

# ===== Holdings =====
with tab_holdings:
    if holdings_status == "unavailable":
        st.warning("Holdings data is **unavailable** for this fund — common for ETFs and very new schemes.")
    else:
        slug = make_slug(selected)
        h = load_holdings([slug])
        s = load_sectors([slug])
        a = load_assets([slug])
        if h.is_empty():
            st.info("No holdings data fetched yet. Refresh from **Settings → Update All Holdings**.")
        else:
            stats = quick_stats(slug)

            st.subheader("Asset & cap breakdown")
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("% Equity", f"{stats['pct_equity']:.1f}%" if stats["pct_equity"] else "—")
            a2.metric("% Debt", f"{stats['pct_debt']:.1f}%" if stats["pct_debt"] else "—")
            a3.metric("% Cash", f"{stats['pct_cash']:.1f}%" if stats["pct_cash"] else "—")
            a4.metric("# Holdings", f"{stats['n_holdings']:,}")

            if stats["pct_largecap"] or stats["pct_midcap"] or stats["pct_smallcap"]:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("% Largecap", f"{stats['pct_largecap']:.1f}%")
                m2.metric("% Midcap", f"{stats['pct_midcap']:.1f}%")
                m3.metric("% Smallcap", f"{stats['pct_smallcap']:.1f}%")
                m4.metric("% Other (eq.)", f"{stats['pct_other_mcap']:.1f}%")

            st.subheader("Concentration")
            c1, c2, c3 = st.columns(3)
            c1.metric("Top 3 holdings", f"{stats['pct_top3']:.1f}%" if stats["pct_top3"] is not None else "—")
            c2.metric("Top 5 holdings", f"{stats['pct_top5']:.1f}%" if stats["pct_top5"] is not None else "—")
            c3.metric("Top 10 holdings", f"{stats['pct_top10']:.1f}%" if stats["pct_top10"] is not None else "—")

            if any(stats["credit_breakdown"].values()):
                st.subheader("Credit-rating breakdown (debt slice)")
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("% Sovereign", f"{stats['pct_sovereign']:.1f}%")
                d2.metric("% AAA / High-grade corp.", f"{stats['pct_corporate_high_grade']:.1f}%")
                d3.metric("% BBB & below (B-rated)", f"{stats['pct_b_rated']:.1f}%")
                d4.metric("% Poor / Unrated", f"{stats['pct_poor_rated']:.1f}%")

            st.divider()
            render_holdings_table(h, s, a, slug, display_name=short_scheme_name(selected))

# ===== Calendar Returns =====
with tab_calendar:
    monthly = nav_pd.resample("ME").last().pct_change().dropna()
    if monthly.empty:
        st.info("Not enough data for calendar returns.")
    else:
        df = pd.DataFrame({"return": monthly.values * 100, "date": monthly.index})
        df["year"] = df["date"].dt.year
        df["month"] = df["date"].dt.month_name().str[:3]
        pivot = df.pivot_table(index="year", columns="month", values="return", aggfunc="mean")
        month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        pivot = pivot.reindex(columns=[m for m in month_order if m in pivot.columns]).sort_index(ascending=False)

        st.subheader("Monthly returns heatmap (%)")
        fig_heat = px.imshow(
            pivot,
            color_continuous_scale="RdYlGn",
            color_continuous_midpoint=0,
            aspect="auto",
            text_auto=".1f",
        )
        fig_heat.update_layout(height=max(280, 28 * len(pivot)))
        st.plotly_chart(fig_heat, use_container_width=True, key="mf-detail-heat")

        st.subheader("Calendar-year returns (%)")
        yearly = nav_pd.resample("YE").last().pct_change().dropna() * 100
        if not yearly.empty:
            yearly_df = pd.DataFrame({"year": yearly.index.year, "return": yearly.values})
            fig_year = px.bar(
                yearly_df,
                x="year",
                y="return",
                color="return",
                color_continuous_scale="RdYlGn",
                color_continuous_midpoint=0,
                text_auto=".1f",
            )
            fig_year.update_layout(height=320, showlegend=False, yaxis_title="Return %")
            st.plotly_chart(fig_year, use_container_width=True, key="mf-detail-year")

# ===== About =====
with tab_about:
    rows = []
    if amfi_row:
        rows.extend(
            [
                ("Scheme Code (AMFI)", amfi_row["scheme_code"]),
                ("ISIN (Growth)", amfi_row["isin_growth"] or "—"),
                ("ISIN (Reinvestment)", amfi_row["isin_reinvestment"] or "—"),
                ("AMFI Latest NAV", amfi_row["nav"]),
                ("AMFI NAV Date", amfi_row["nav_date"]),
            ]
        )
    if meta:
        rows.extend(
            [
                ("Asset Class", meta.get("assetClass") or "—"),
                ("Status", meta.get("status") or "—"),
                ("Fund Manager", meta.get("fundManager") or "—"),
                ("Launch Date", meta.get("launchDate") or "—"),
                ("AUM as of", meta.get("aumAsOf") or "—"),
                ("Expense Ratio as of", meta.get("expenseRatioAsOf") or "—"),
                ("Min Investment (₹)", meta.get("minInvestment")),
                ("Min Top-up (₹)", meta.get("minTopup")),
                ("Turnover Ratio %", meta.get("turnoverRatio")),
                ("Exit Load", meta.get("exitLoad") or "—"),
                ("Source URL", meta.get("sourceUrl") or "—"),
                (
                    "Metadata Fetched At",
                    fetched.strftime("%Y-%m-%d %H:%M")
                    if isinstance(fetched := meta.get("fetchedAt"), datetime)
                    else "—",
                ),
            ]
        )

    rows.extend(
        [
            ("Slug (computed)", make_slug(selected)),
            ("NAV history days", len(nav_pd)),
            ("First NAV date", str(nav_pd.index.min().date())),
            ("Last NAV date", str(nav_pd.index.max().date())),
            ("Latest NAV", f"₹ {nav_pd.iloc[-1]:.4f}"),
        ]
    )

    about_df = pd.DataFrame(rows, columns=["Field", "Value"])
    st.dataframe(about_df, use_container_width=True, hide_index=True)
