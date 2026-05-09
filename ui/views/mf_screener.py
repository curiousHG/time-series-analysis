"""Mutual Fund Screener — browse the AMFI universe, filter, add to library."""

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_count, load_amfi_df
from data.repositories.metadata import load_metadata_all
from mutual_funds.display import detect_option, detect_plan, make_slug
from mutual_funds.holdings_stats import quick_stats
from services.registry_service import backfill_missing, list_tracked
from ui.state.loaders import load_metrics_cached

st.title("Mutual Fund Screener")


@st.cache_data(ttl=300, show_spinner=False)
def _load_screener_df() -> pl.DataFrame:
    amfi = load_amfi_df()
    if amfi.is_empty():
        return amfi

    # Derive plan + option from scheme_name (cached so we only parse once per 5 min)
    amfi = amfi.with_columns(
        pl.col("scheme_name").map_elements(detect_plan, return_dtype=pl.Utf8).alias("plan"),
        pl.col("scheme_name").map_elements(detect_option, return_dtype=pl.Utf8).alias("option"),
    )

    meta = load_metadata_all()
    if meta.height:
        meta = meta.select(
            pl.col("schemeName").alias("scheme_name"),
            pl.col("aumCrores").alias("aum_crores"),
            pl.col("expenseRatio").alias("expense_ratio"),
            pl.col("benchmark"),
            pl.col("category").alias("metadata_category"),
            pl.col("assetClass").alias("asset_class"),
        )
        amfi = amfi.join(meta, on="scheme_name", how="left")
        amfi = amfi.with_columns(pl.coalesce([pl.col("metadata_category"), pl.col("category")]).alias("category")).drop(
            "metadata_category"
        )
    else:
        amfi = amfi.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("aum_crores"),
            pl.lit(None, dtype=pl.Float64).alias("expense_ratio"),
            pl.lit(None, dtype=pl.Utf8).alias("benchmark"),
            pl.lit(None, dtype=pl.Utf8).alias("asset_class"),
        )

    metrics = load_metrics_cached()
    if metrics.height:
        amfi = amfi.join(metrics, on="scheme_name", how="left")
    else:
        amfi = amfi.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("cagr_1y"),
            pl.lit(None, dtype=pl.Float64).alias("cagr_3y"),
            pl.lit(None, dtype=pl.Float64).alias("cagr_5y"),
            pl.lit(None, dtype=pl.Float64).alias("cagr_10y"),
            pl.lit(None, dtype=pl.Float64).alias("vol_1y"),
            pl.lit(None, dtype=pl.Float64).alias("sharpe_1y"),
            pl.lit(None, dtype=pl.Float64).alias("max_dd_1y"),
            pl.lit(None, dtype=pl.Float64).alias("pct_from_ath"),
        )

    tracked = list_tracked()
    if tracked.height:
        tracked = tracked.select(
            pl.col("schemeName").alias("scheme_name"),
            pl.col("navStatus").alias("nav_status"),
            pl.col("holdingsStatus").alias("holdings_status"),
            pl.col("metadataStatus").alias("metadata_status"),
        )
        amfi = amfi.join(tracked, on="scheme_name", how="left")
    else:
        amfi = amfi.with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("nav_status"),
            pl.lit(None, dtype=pl.Utf8).alias("holdings_status"),
            pl.lit(None, dtype=pl.Utf8).alias("metadata_status"),
        )

    # Holdings-derived stats — compute only for funds where holdings_status='available'
    if tracked is not None and amfi.filter(pl.col("holdings_status") == "available").height > 0:
        with_holdings = amfi.filter(pl.col("holdings_status") == "available")["scheme_name"].to_list()
        rows = []
        for name in with_holdings:
            try:
                qs_stats = quick_stats(make_slug(name))
            except Exception:
                continue
            rows.append(
                {
                    "scheme_name": name,
                    "pct_equity": qs_stats.get("pct_equity") or None,
                    "pct_debt": qs_stats.get("pct_debt") or None,
                    "pct_cash": qs_stats.get("pct_cash") or None,
                    "pct_top10": qs_stats.get("pct_top10"),
                }
            )
        if rows:
            holdings_df = pl.DataFrame(rows)
            amfi = amfi.join(holdings_df, on="scheme_name", how="left")
        else:
            amfi = amfi.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("pct_equity"),
                pl.lit(None, dtype=pl.Float64).alias("pct_debt"),
                pl.lit(None, dtype=pl.Float64).alias("pct_cash"),
                pl.lit(None, dtype=pl.Float64).alias("pct_top10"),
            )
    else:
        amfi = amfi.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("pct_equity"),
            pl.lit(None, dtype=pl.Float64).alias("pct_debt"),
            pl.lit(None, dtype=pl.Float64).alias("pct_cash"),
            pl.lit(None, dtype=pl.Float64).alias("pct_top10"),
        )

    return amfi


def _tracked_label(row: dict) -> str:
    statuses = [row.get("nav_status"), row.get("holdings_status"), row.get("metadata_status")]
    if all(s is None for s in statuses):
        return "—"
    available = sum(1 for s in statuses if s == "available")
    if available == 3:
        return "✓"
    if available == 0:
        return "✗"
    return f"◐ {available}/3"


def _apply_filters(
    df: pl.DataFrame,
    *,
    name_query: str,
    amcs: list[str],
    cats: list[str],
    plans: list[str],
    options: list[str],
    aum_min: float,
    ter_max: float,
    only_tracked: bool,
    has_nav: bool,
    cagr_min: float | None = None,
    sharpe_min: float | None = None,
    dd_min: float | None = None,
) -> pl.DataFrame:
    out = df
    if name_query:
        # AND across whitespace-separated tokens, case-insensitive substring per token.
        for token in name_query.split():
            out = out.filter(pl.col("scheme_name").str.contains(f"(?i){token}"))
    if amcs:
        out = out.filter(pl.col("fund_house").is_in(amcs))
    if cats:
        out = out.filter(pl.col("category").is_in(cats))
    if plans:
        out = out.filter(pl.col("plan").is_in(plans))
    if options:
        out = out.filter(pl.col("option").is_in(options))
    if aum_min > 0:
        out = out.filter(pl.col("aum_crores").fill_null(0) >= aum_min)
    if ter_max < 5.0:
        out = out.filter(pl.col("expense_ratio").fill_null(0) <= ter_max)
    if only_tracked:
        out = out.filter(pl.col("nav_status").is_not_null())
    if has_nav:
        out = out.filter(pl.col("cagr_1y").is_not_null())
        if cagr_min is not None:
            out = out.filter(pl.col("cagr_1y") * 100 >= cagr_min)
        if sharpe_min is not None:
            out = out.filter(pl.col("sharpe_1y") >= sharpe_min)
        if dd_min is not None:
            out = out.filter(pl.col("max_dd_1y") * 100 >= dd_min)
    return out


# ---- universe summary
amfi_count = get_scheme_count()
tracked_df = list_tracked()
tracked_count = tracked_df.height
complete_count = (
    tracked_df.filter((pl.col("navStatus") == "available") & (pl.col("metadataStatus") == "available")).height
    if tracked_count
    else 0
)

m1, m2 = st.columns(2)
m1.metric("AMFI universe", f"{amfi_count:,}")
m2.metric(
    "Tracked",
    f"{tracked_count:,}",
    delta=f"{complete_count} with NAV+metadata",
    delta_color="off",
)

if amfi_count == 0:
    st.warning("AMFI master data not loaded. Run **Sync AMFI Master** from Settings first.")
    st.stop()

df = _load_screener_df()

# ---- sidebar filters
with st.sidebar:
    st.header("Filters")
    amc_options = sorted(df["fund_house"].drop_nulls().unique().to_list())
    cat_options = sorted(df["category"].drop_nulls().unique().to_list())

    name_query = st.text_input(
        "Search by name",
        placeholder="e.g. parag parikh flexi",
        help="Case-insensitive substring match. Multiple words = AND (all must appear).",
    )
    amcs = st.multiselect("AMC", amc_options)
    cats = st.multiselect("Category", cat_options)
    plans = st.multiselect("Plan", ["Direct", "Regular"])
    options = st.multiselect("Option", ["Growth", "IDCW", "Bonus", "ETF", "Other"])
    aum_min = st.number_input("Min AUM (₹ Cr)", min_value=0, value=0, step=100)
    ter_max = st.number_input("Max TER %", min_value=0.0, value=2.5, step=0.05, format="%.2f")
    only_tracked = st.checkbox("Only my tracked funds")
    has_nav = st.checkbox("Has NAV history (enables risk filters)")

    cagr_min = sharpe_min = dd_min = None
    if has_nav:
        cagr_min = st.slider("Min 1Y CAGR %", -50, 100, -50)
        sharpe_min = st.slider("Min Sharpe", -2.0, 4.0, -2.0, step=0.1)
        dd_min = st.slider("Max drawdown ≥ (%)", -100, 0, -100)

filtered = _apply_filters(
    df,
    name_query=name_query.strip() if name_query else "",
    amcs=amcs,
    cats=cats,
    plans=plans,
    options=options,
    aum_min=aum_min,
    ter_max=ter_max,
    only_tracked=only_tracked,
    has_nav=has_nav,
    cagr_min=cagr_min,
    sharpe_min=sharpe_min,
    dd_min=dd_min,
)

st.caption(f"{filtered.height:,} of {df.height:,} schemes match")

# Build display DataFrame with Tracked column
display_rows = filtered.to_pandas()
display_rows["Tracked"] = display_rows.apply(
    lambda r: _tracked_label(
        {
            "nav_status": r.get("nav_status"),
            "holdings_status": r.get("holdings_status"),
            "metadata_status": r.get("metadata_status"),
        }
    ),
    axis=1,
)

display_cols = [
    "scheme_name",
    "fund_house",
    "category",
    "plan",
    "option",
    "aum_crores",
    "expense_ratio",
    "benchmark",
    "cagr_1y",
    "cagr_3y",
    "cagr_5y",
    "cagr_10y",
    "vol_1y",
    "sharpe_1y",
    "max_dd_1y",
    "pct_from_ath",
    "pct_equity",
    "pct_debt",
    "pct_cash",
    "pct_top10",
    "Tracked",
]
display_rows = display_rows[[c for c in display_cols if c in display_rows.columns]]
display_rows = display_rows.rename(
    columns={
        "scheme_name": "Scheme",
        "fund_house": "AMC",
        "category": "Category",
        "plan": "Plan",
        "option": "Option",
        "aum_crores": "AUM (₹ Cr)",
        "expense_ratio": "TER %",
        "benchmark": "Benchmark",
        "cagr_1y": "1Y CAGR %",
        "cagr_3y": "3Y CAGR %",
        "cagr_5y": "5Y CAGR %",
        "cagr_10y": "10Y CAGR %",
        "vol_1y": "1Y Vol %",
        "sharpe_1y": "Sharpe",
        "max_dd_1y": "Max DD %",
        "pct_from_ath": "% from ATH",
        "pct_equity": "% Equity",
        "pct_debt": "% Debt",
        "pct_cash": "% Cash",
        "pct_top10": "% Top10",
    }
)

# Display percentage columns as percentages (multiply by 100)
for col in ["1Y CAGR %", "3Y CAGR %", "5Y CAGR %", "10Y CAGR %", "1Y Vol %", "Max DD %", "% from ATH"]:
    if col in display_rows.columns:
        display_rows[col] = display_rows[col] * 100

st.dataframe(
    display_rows,
    use_container_width=True,
    hide_index=True,
    column_config={
        "AUM (₹ Cr)": st.column_config.NumberColumn(format="%.0f"),
        "TER %": st.column_config.NumberColumn(format="%.2f"),
        "1Y CAGR %": st.column_config.NumberColumn(format="%+.1f"),
        "3Y CAGR %": st.column_config.NumberColumn(format="%+.1f"),
        "5Y CAGR %": st.column_config.NumberColumn(format="%+.1f"),
        "10Y CAGR %": st.column_config.NumberColumn(format="%+.1f"),
        "1Y Vol %": st.column_config.NumberColumn(format="%.1f"),
        "Sharpe": st.column_config.NumberColumn(format="%.2f"),
        "Max DD %": st.column_config.NumberColumn(format="%+.1f"),
        "% from ATH": st.column_config.NumberColumn(format="%+.1f"),
        "% Equity": st.column_config.NumberColumn(format="%.1f"),
        "% Debt": st.column_config.NumberColumn(format="%.1f"),
        "% Cash": st.column_config.NumberColumn(format="%.1f"),
        "% Top10": st.column_config.NumberColumn(format="%.1f"),
    },
)

# ---- Fetch NAV + metadata for the top-N of the current filtered view
if filtered.height > 0:
    st.divider()
    st.markdown(f"#### Fetch data for filtered view ({filtered.height:,} funds)")
    st.caption(
        "Pulls NAV + metadata for the top-N rows of the filtered view (in the order shown). "
        "Throttled to ~3 req/s (2 concurrent · 0.4 s submission delay) to avoid hitting "
        "upstream rate limits. Already-`available` sources are skipped."
    )

    max_n = min(500, filtered.height)
    default_n = min(50, filtered.height)
    bc1, bc2 = st.columns([1, 3], vertical_alignment="bottom")
    with bc1:
        batch = st.number_input(
            "Top N",
            min_value=1,
            max_value=max_n,
            value=default_n,
            step=10,
        )
    with bc2:
        run_clicked = st.button(
            f"Fetch for top {batch} filtered funds",
            type="primary",
            key="screener_backfill",
            use_container_width=True,
        )

    if run_clicked:
        picked_names = filtered["scheme_name"].head(int(batch)).to_list()
        total_items = int(batch) * 2  # nav + metadata per fund
        progress = st.progress(0.0, text="Starting…")

        def _cb(done: int, total: int, name: str, source: str) -> None:
            progress.progress(done / total, text=f"[{done}/{total}] {source}: {name[:60]}")

        with st.spinner(f"Fetching NAV/metadata for {len(picked_names)} fund(s)…"):
            result = backfill_missing(
                scheme_names=picked_names,
                max_per_run=total_items,
                progress_cb=_cb,
            )
        progress.progress(1.0, text="Done")
        _load_screener_df.clear()
        load_metrics_cached.clear()
        st.success(f"Fetched {len(result['fetched'])} · failed {len(result['failed'])}")
        st.rerun()
