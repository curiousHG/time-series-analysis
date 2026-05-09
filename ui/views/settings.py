"""Settings — tradebook drop-in, AMFI sync, NAV/Holdings refresh, DB stats."""

import pandas as pd
import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_count, sync_amfi_master
from data.repositories.holdings import (
    load_holdings,
    save_assets,
    save_holdings,
    save_sectors,
)
from data.repositories.nav import (
    _fetch_single_nav,
    _load_scheme_code_map,
    _save_scheme_code_map,
    load_nav_df,
    save_nav_df,
)
from data.repositories.tradebook import get_tradebook_stats, import_tradebook_bytes, load_tradebook_from_db
from mutual_funds.display import make_slug
from mutual_funds.holdings import (
    normalize_asset_allocation,
    normalize_holdings,
    normalize_sector_allocation,
)
from services.data_freshness import compute_holdings_freshness, compute_nav_freshness
from services.db_stats import get_db_stats
from services.registry_service import list_tracked, retry_unavailable
from services.scheme_lookup import resolve_tradebook
from ui.components.freshness_banner import clear_freshness_cache
from ui.state.loaders import get_short_names, load_holdings_data, load_nav_data, load_txn_data

st.title("Settings")

# ==== Tradebook (portfolio drop-in) ====
st.subheader("Portfolio Tradebook")

stats = get_tradebook_stats()
if stats["total_trades"] > 0:
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Total Trades", stats["total_trades"])
    col_b.metric("Buys / Sells", f"{stats['buys']} / {stats['sells']}")
    col_c.metric("Symbols", stats["symbols"])
    col_d.metric("Date Range", f"{stats['first_date']} to {stats['last_date']}")
else:
    st.info("No tradebook data in database. Upload a CSV below.")

uploaded = st.file_uploader(
    "Upload Kite/Zerodha tradebook CSV",
    type=["csv"],
    key="tradebook_upload",
)

if uploaded is not None:
    with st.spinner("Importing tradebook..."):
        new_count, skipped = import_tradebook_bytes(uploaded.getvalue())
    if new_count > 0:
        st.success(f"Imported **{new_count}** new trades, skipped **{skipped}** duplicates.")
        load_txn_data.clear()
    elif skipped > 0:
        st.warning(f"All **{skipped}** trades already exist.")
    else:
        st.error("CSV was empty or could not be parsed.")

# Live ISIN resolution preview (replaces the old fund_mapping persist+button flow)
if stats["total_trades"] > 0:
    tb = load_tradebook_from_db()
    if not tb.is_empty():
        resolved = resolve_tradebook(tb).select(["symbol", "isin", "scheme_name", "scheme_code"]).unique()
        matched = resolved.filter(pl.col("scheme_name").is_not_null()).height
        unmatched = resolved.height - matched
        c1, c2 = st.columns(2)
        c1.metric("Resolved by ISIN", matched)
        c2.metric("Unresolved", unmatched)
        with st.expander("ISIN → scheme resolution"):
            st.dataframe(resolved.to_pandas(), use_container_width=True, hide_index=True)
        if unmatched > 0 and get_scheme_count() == 0:
            st.warning("AMFI master is empty — sync below to enable ISIN resolution.")

# ==== AMFI master ====
st.divider()
st.subheader("AMFI Master Data")

amfi_count = get_scheme_count()
st.write(f"**{amfi_count:,}** schemes in database")

if st.button("Sync AMFI Master", type="primary"):
    with st.spinner("Downloading AMFI NAVAll.txt..."):
        count = sync_amfi_master()
    st.success(f"Synced **{count:,}** schemes from AMFI")
    st.rerun()

# ==== NAV / Holdings refresh for tracked funds ====
st.divider()
st.subheader("Refresh tracked-fund data")

tracked = list_tracked()
if tracked.height == 0:
    st.info("No tracked funds yet. Add some via the **MF Screener** page.")
else:
    scheme_names = tracked["schemeName"].to_list()
    short_by_name = get_short_names(tuple(scheme_names))

    with st.spinner(f"Computing freshness for {len(scheme_names):,} tracked fund(s)…"):
        nav_report = compute_nav_freshness(scheme_names, [make_slug(n) for n in scheme_names])
        holdings_report = compute_holdings_freshness(scheme_names, [make_slug(n) for n in scheme_names])

    _STATUS_STYLES = {
        "Fresh": "background-color: #86efac; color: #14532d",
        "Stale": "background-color: #fde68a; color: #78350f",
        "Missing": "background-color: #fca5a5; color: #7f1d1d",
        "Available": "background-color: #86efac; color: #14532d",
        "Pending": "background-color: #fde68a; color: #78350f",
        "Unavailable": "background-color: #fca5a5; color: #7f1d1d",
    }

    def _color_status(val: str) -> str:
        return _STATUS_STYLES.get(val, "")

    st.caption(f"Current date: {nav_report.current_date}")

    with st.spinner("Loading NAV and holdings data…"):
        nav_df = load_nav_df(scheme_names)
        holdings_df = load_holdings([make_slug(n) for n in scheme_names])

    nav_rows = []
    for row in nav_report.rows:
        scheme_nav = nav_df.filter(pl.col("schemeName") == row.scheme_name)
        first_date = str(scheme_nav.select("date").to_series().min()) if scheme_nav.height > 0 else "-"
        nav_rows.append(
            {
                "Fund": short_by_name.get(row.scheme_name, row.scheme_name),
                "Records": scheme_nav.height,
                "First Date": first_date,
                "Last Date": str(row.last_date) if row.last_date else "-",
                "Days Old": row.days_old,
                "Status": row.status.capitalize(),
            }
        )

    st.markdown("**NAV Status**")
    st.write(f"Stale: **{nav_report.stale_count} / {nav_report.total}**")
    nav_pd = pd.DataFrame(nav_rows)
    st.dataframe(
        nav_pd.style.map(_color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )

    holdings_rows = []
    for row in holdings_report.rows:
        slug = make_slug(row.scheme_name)
        scheme_holdings = holdings_df.filter(pl.col("schemeSlug") == slug)
        holdings_rows.append(
            {
                "Fund": short_by_name.get(row.scheme_name, row.scheme_name),
                "Holdings Count": scheme_holdings.height,
                "Last Portfolio Date": str(row.last_date) if row.last_date else "-",
                "Days Old": row.days_old,
                "Status": row.status.capitalize(),
            }
        )

    st.markdown("**Holdings Status**")
    st.write(f"Stale: **{holdings_report.stale_count} / {holdings_report.total}**")
    holdings_pd = pd.DataFrame(holdings_rows)
    st.dataframe(
        holdings_pd.style.map(_color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )

    # Update controls
    col1, col2, col3 = st.columns(3)
    update_nav = col1.button("Update All NAV", type="primary", use_container_width=True)
    update_holdings = col2.button("Update All Holdings", type="secondary", use_container_width=True)
    update_all = col3.button("Update Everything", type="secondary", use_container_width=True)

    if update_nav or update_all:
        st.markdown("#### NAV Update Progress")
        code_map = _load_scheme_code_map()
        last_date_by_scheme = {row.scheme_name: row.last_date for row in nav_report.rows}

        total = len(scheme_names)
        progress = st.progress(0.0, text=f"Starting NAV updates for {total} fund(s)…")
        counter_slot = st.empty()
        log_slot = st.empty()  # rolling tail of recent updates
        recent: list[str] = []

        new_records_total = 0
        updated_count = 0
        skipped_count = 0
        failed: list[tuple[str, str]] = []

        for i, name in enumerate(scheme_names):
            short = short_by_name.get(name, name)
            progress.progress(i / total, text=f"[{i + 1}/{total}] {short}")
            try:
                df = _fetch_single_nav(name, code_map)
                last_known = last_date_by_scheme.get(name)
                api_max = df.select("date").to_series().max() if df.height > 0 else None

                if last_known is not None:
                    df = df.filter(pl.col("date") > last_known)

                if df.height == 0:
                    skipped_count += 1
                    if api_max is None:
                        recent.append(f"⚠️ {short} — source returned no rows")
                    else:
                        recent.append(f"• {short} — up to date ({api_max})")
                else:
                    save_nav_df(df)
                    dates = df.select("date").to_series()
                    new_records_total += df.height
                    updated_count += 1
                    recent.append(f"✓ {short} — {df.height} new rows ({dates.min()} → {dates.max()})")
            except Exception as e:
                failed.append((short, str(e)))
                recent.append(f"✗ {short} — {e}")

            counter_slot.markdown(
                f"**Updated:** {updated_count} · **Skipped:** {skipped_count} · "
                f"**Failed:** {len(failed)} · **New rows:** {new_records_total:,}"
            )
            log_slot.code("\n".join(recent[-8:]), language=None)  # last 8 lines

        progress.progress(1.0, text=f"Done — {updated_count} updated, {skipped_count} skipped, {len(failed)} failed")
        _save_scheme_code_map(code_map)
        load_nav_data.clear()
        clear_freshness_cache()
        if failed:
            with st.expander(f"{len(failed)} failure(s)"):
                for short, err in failed:
                    st.error(f"**{short}** — {err}")
        st.toast(f"NAV: {updated_count} updated ({new_records_total} new rows), {skipped_count} skipped")
        st.rerun()

    if update_holdings or update_all:
        st.markdown("#### Holdings Update Progress")
        from sqlmodel import delete

        from core.database import get_session
        from core.models import MfAssetAllocation, MfHolding, MfSectorAllocation

        scheme_slugs = [make_slug(n) for n in scheme_names]
        with st.spinner("Clearing existing holdings rows…"), get_session() as session:
            session.exec(delete(MfHolding).where(MfHolding.scheme_slug.in_(scheme_slugs)))
            session.exec(delete(MfSectorAllocation).where(MfSectorAllocation.scheme_slug.in_(scheme_slugs)))
            session.exec(delete(MfAssetAllocation).where(MfAssetAllocation.scheme_slug.in_(scheme_slugs)))
            session.commit()

        total = len(scheme_slugs)
        progress = st.progress(0.0, text=f"Starting holdings updates for {total} fund(s)…")
        counter_slot = st.empty()
        log_slot = st.empty()
        recent: list[str] = []

        success_count = 0
        failed: list[tuple[str, str]] = []
        total_holdings = 0

        for i, (name, slug) in enumerate(zip(scheme_names, scheme_slugs, strict=False)):
            short = short_by_name.get(name, name)
            progress.progress(i / total, text=f"[{i + 1}/{total}] {short}")
            try:
                from data.fetchers.mutual_fund import fetch_portfolio_by_slug

                resp = fetch_portfolio_by_slug(slug)
                h = normalize_holdings(resp, slug)
                s = normalize_sector_allocation(resp, slug)
                a = normalize_asset_allocation(resp, slug)
                save_holdings(h)
                save_sectors(s)
                save_assets(a)
                success_count += 1
                total_holdings += h.height
                recent.append(f"✓ {short} — {h.height} holdings · {s.height} sectors · {a.height} asset types")
            except Exception as e:
                failed.append((short, str(e)))
                recent.append(f"✗ {short} — {e}")

            counter_slot.markdown(
                f"**Updated:** {success_count} · **Failed:** {len(failed)} · **Holdings rows:** {total_holdings:,}"
            )
            log_slot.code("\n".join(recent[-8:]), language=None)

        progress.progress(1.0, text=f"Done — {success_count} updated, {len(failed)} failed")
        load_holdings_data.clear()
        clear_freshness_cache()
        if failed:
            with st.expander(f"{len(failed)} failure(s)"):
                for short, err in failed:
                    st.error(f"**{short}** — {err}")
        st.toast(f"Saved holdings for {success_count}/{total} funds")
        st.rerun()

    # Retry unavailable
    unavailable_funds = tracked.filter(
        (pl.col("navStatus") == "unavailable")
        | (pl.col("holdingsStatus") == "unavailable")
        | (pl.col("metadataStatus") == "unavailable")
    )
    if unavailable_funds.height > 0:
        st.markdown("**Retry unavailable sources**")
        sel = st.selectbox(
            "Fund",
            options=unavailable_funds["schemeName"].to_list(),
            format_func=lambda n: short_by_name.get(n, n),
            key="retry_pick",
        )
        if st.button("Retry"):
            with st.spinner(f"Retrying {short_by_name.get(sel, sel)}…"):
                results = retry_unavailable(sel)
            if results:
                st.success(" · ".join(f"{k}: {v}" for k, v in results.items()))
            else:
                st.info("Nothing to retry — all sources already available or pending.")
            st.rerun()

# ==== Data sources reference ====
st.divider()
st.subheader("Data Sources")
st.caption("Where each kind of data comes from, what input is required to fetch it, and where it lands.")

_DATA_SOURCES = pd.DataFrame(
    [
        {
            "Source": "AMFI NAVAll.txt",
            "What it returns": "Master list of every Indian MF scheme (code, name, ISINs, latest NAV, AMC, category)",
            "Input": "None — single bulk download",
            "Example": "sync_amfi_master()",
            "Lands in": "amfi_schemes",
        },
        {
            "Source": "MFAPI (api.mfapi.in)",
            "What it returns": "Full historical NAV time series for one scheme",
            "Input": "scheme_code (int, AMFI-issued)",
            "Example": 'fetch_nav_from_mfapi("122639", "Parag Parikh Flexi Cap…")',
            "Lands in": "mf_nav",
        },
        {
            "Source": "AdvisorKhoj — portfolio page",
            "What it returns": "Holdings (each stock + weight + ISIN), sector allocation, asset allocation",
            "Input": "scheme_slug (computed from name via make_slug)",
            "Example": 'fetch_portfolio_by_slug("parag-parikh-flexi-cap-fund-…")',
            "Lands in": "mf_holdings, mf_sector_allocation, mf_asset_allocation",
        },
        {
            "Source": "AdvisorKhoj — overview page",
            "What it returns": "AUM, TER, benchmark, launch date, exit load, category, asset class, min investment, turnover",
            "Input": "scheme_name (slug derived internally)",
            "Example": 'fetch_fund_metadata("Parag Parikh Flexi Cap…")',
            "Lands in": "mf_metadata",
        },
        {
            "Source": "AMFI fuzzy search (local DB, pg_trgm)",
            "What it returns": "Trigram-similarity ranked scheme names + code + ISIN + AMC + category",
            "Input": "free-text query ≥ 2 chars",
            "Example": 'search_amfi("hdfc top 100")',
            "Lands in": "(query-time only)",
        },
        {
            "Source": "yfinance",
            "What it returns": "Daily OHLCV bars (global tickers, indices including ^NSEI)",
            "Input": "symbol + optional start/end dates",
            "Example": 'ensure_stock_data("^NSEI", date(2020,1,1), date(2026,5,9))',
            "Lands in": "stock_ohlcv",
        },
        {
            "Source": "jugaad-data (NSE bhavcopy)",
            "What it returns": "OHLCV from NSE for Indian symbols (tried before yfinance for .NS)",
            "Input": "symbol without .NS suffix, date range",
            "Example": 'ensure_stock_data("RELIANCE", …)',
            "Lands in": "stock_ohlcv",
        },
        {
            "Source": "Kite/Zerodha tradebook CSV",
            "What it returns": "One row per trade (trade_id, symbol, isin, date, qty, price, type)",
            "Input": "CSV bytes via file upload",
            "Example": "import_tradebook_bytes(uploaded.getvalue())",
            "Lands in": "mf_tradebook",
        },
    ]
)
st.dataframe(_DATA_SOURCES, use_container_width=True, hide_index=True)

with st.expander("How fetches chain together"):
    st.markdown(
        """
**Adding a fund** (Screener → *Add selected*) calls `services.registry_service.add_funds([name])`, which fans out in parallel:
- MFAPI using `amfi_schemes.scheme_code` for that name → fills **mf_nav**
- AdvisorKhoj portfolio page (slug = `make_slug(name)`) → fills **mf_holdings**, **mf_sector_allocation**, **mf_asset_allocation**
- AdvisorKhoj overview page → fills **mf_metadata**

All three results write back `available` / `unavailable` into the corresponding status column on the **mf_registry** row.

**Tradebook upload** writes raw rows to **mf_tradebook**. Resolution to scheme names is done **live in memory** by joining `mf_tradebook.isin = amfi_schemes.isin_growth` (no `fund_mapping` table any more).

**The glue**: every external system is reachable from any of `amfi_schemes.scheme_name ⇄ scheme_code ⇄ isin_growth`.
        """
    )

# ==== Database statistics ====
st.divider()
st.subheader("Database Statistics")

db_stats = get_db_stats()
m1, m2, m3 = st.columns(3)
m1.metric("Database", db_stats.db_name)
m2.metric("Total size", db_stats.db_pretty)
m3.metric("Tables", db_stats.table_count)

table_stats_pd = pd.DataFrame(
    [
        {
            "Table": t.name,
            "Rows": t.rows,
            "Size": t.total_pretty,
            "Bytes": t.total_bytes,
        }
        for t in db_stats.tables
    ]
)
st.dataframe(
    table_stats_pd,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Rows": st.column_config.NumberColumn(format="%d"),
        "Bytes": st.column_config.NumberColumn(help="Total bytes (table + indexes + toast)", format="%d"),
    },
)
