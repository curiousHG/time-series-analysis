import traceback

import pandas as pd
import polars as pl
import streamlit as st

from data.fetchers.mutual_fund import fetch_portfolio_by_slug
from data.repositories.amfi import get_scheme_count, sync_amfi_master
from data.repositories.fund_mapping import auto_map_tradebook
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
from data.repositories.registry import load_registry, save_to_registry
from data.repositories.tradebook import get_tradebook_stats, import_tradebook_bytes
from mutual_funds.holdings import (
    normalize_asset_allocation,
    normalize_holdings,
    normalize_sector_allocation,
)
from services.data_freshness import compute_holdings_freshness, compute_nav_freshness
from services.db_stats import get_db_stats
from ui.components.freshness_banner import clear_freshness_cache
from ui.components.fund_picker import fund_picker
from ui.state.loaders import load_holdings_data, load_nav_data, load_txn_data
from ui.utils import get_selected_registry

st.title("Data Manager")

# ---- Fund picker in sidebar
fund_picker(
    load_registry=load_registry,
    save_to_registry=save_to_registry,
)

selected_registry = get_selected_registry(load_registry)

if selected_registry.height == 0:
    st.info("Select funds from the sidebar to manage their data.")
    st.stop()

scheme_names = selected_registry["schemeName"].to_list()
scheme_slugs = selected_registry["schemeSlug"].to_list()
short_by_name = (
    dict(zip(selected_registry["schemeName"].to_list(), selected_registry["shortName"].to_list(), strict=False))
    if "shortName" in selected_registry.columns
    else {n: n for n in scheme_names}
)

# ---- Load existing data from DB to show status
nav_df = load_nav_df(scheme_names)
holdings_df = load_holdings(scheme_slugs)

nav_report = compute_nav_freshness(scheme_names, scheme_slugs)
holdings_report = compute_holdings_freshness(scheme_names, scheme_slugs)

_STATUS_STYLES = {
    "Fresh": "background-color: #86efac; color: #14532d",
    "Stale": "background-color: #fde68a; color: #78350f",
    "Missing": "background-color: #fca5a5; color: #7f1d1d",
}


def _color_status(val: str) -> str:
    return _STATUS_STYLES.get(val, "")


st.caption(f"Current date: {nav_report.current_date}")

# ---- NAV status table
st.subheader("NAV Data Status")
st.write(f"Stale: **{nav_report.stale_count} / {nav_report.total}**")

nav_status_rows = []
for row in nav_report.rows:
    scheme_nav = nav_df.filter(pl.col("schemeName") == row.scheme_name)
    first_date = str(scheme_nav.select("date").to_series().min()) if scheme_nav.height > 0 else "-"
    nav_status_rows.append(
        {
            "Fund": short_by_name.get(row.scheme_name, row.scheme_name),
            "Slug": row.slug,
            "Records": scheme_nav.height,
            "First Date": first_date,
            "Last Date": str(row.last_date) if row.last_date else "-",
            "Days Old": row.days_old,
            "Status": row.status.capitalize(),
        }
    )

nav_status_pd = pl.DataFrame(nav_status_rows).to_pandas()
st.dataframe(
    nav_status_pd.style.map(_color_status, subset=["Status"]),
    use_container_width=True,
    hide_index=True,
)

# ---- Holdings status
st.subheader("Holdings Data Status")
st.write(f"Stale: **{holdings_report.stale_count} / {holdings_report.total}**")

holdings_status_rows = []
for row in holdings_report.rows:
    scheme_holdings = holdings_df.filter(pl.col("schemeSlug") == row.slug)
    holdings_status_rows.append(
        {
            "Fund": short_by_name.get(row.scheme_name, row.scheme_name),
            "Slug": row.slug,
            "Holdings Count": scheme_holdings.height,
            "Last Portfolio Date": str(row.last_date) if row.last_date else "-",
            "Days Old": row.days_old,
            "Status": row.status.capitalize(),
        }
    )

holdings_status_pd = pl.DataFrame(holdings_status_rows).to_pandas()
st.dataframe(
    holdings_status_pd.style.map(_color_status, subset=["Status"]),
    use_container_width=True,
    hide_index=True,
)

# ---- Update controls
st.divider()
st.subheader("Update Data")

col1, col2, col3 = st.columns(3)
update_nav = col1.button("Update All NAV", type="primary", use_container_width=True)
update_holdings = col2.button("Update All Holdings", type="secondary", use_container_width=True)
update_all = col3.button("Update Everything", type="secondary", use_container_width=True)

if update_nav or update_all:
    st.markdown("#### NAV Update Progress")
    code_map = _load_scheme_code_map()

    last_date_by_scheme = {row.scheme_name: row.last_date for row in nav_report.rows}

    progress = st.progress(0, text="Starting NAV updates...")
    new_records_total = 0
    updated_count = 0
    skipped_count = 0

    for i, name in enumerate(scheme_names):
        progress.progress(i / len(scheme_names), text=f"Fetching NAV: {name}")

        try:
            df = _fetch_single_nav(name, code_map)
            last_known = last_date_by_scheme.get(name)
            api_max = df.select("date").to_series().max() if df.height > 0 else None

            if last_known is not None:
                df = df.filter(pl.col("date") > last_known)

            if df.height == 0:
                if api_max is None:
                    st.warning(f"**{name}** -- source returned no NAV rows")
                elif last_known is not None and api_max <= last_known:
                    st.info(f"**{name}** -- source up to {api_max}; DB already at {last_known}")
                else:
                    st.info(f"**{name}** -- already up to date (source: {api_max})")
                skipped_count += 1
                continue

            save_nav_df(df)
            dates = df.select("date").to_series()
            st.success(f"**{name}** -- {df.height} new rows, {dates.min()} to {dates.max()}")
            new_records_total += df.height
            updated_count += 1
        except Exception as e:
            st.error(f"**{name}** -- Failed: {e}")
            with st.expander("Error details"):
                st.code(traceback.format_exc())

    progress.progress(1.0, text="Done!")
    _save_scheme_code_map(code_map)
    load_nav_data.clear()
    clear_freshness_cache()
    st.toast(f"NAV: {updated_count} updated ({new_records_total} new rows), {skipped_count} already current")
    st.rerun()


if update_holdings or update_all:
    st.markdown("#### Holdings Update Progress")

    # Delete existing holdings for selected slugs
    from sqlmodel import delete

    from core.database import get_session
    from core.models import MfAssetAllocation, MfHolding, MfSectorAllocation

    with get_session() as session:
        session.exec(delete(MfHolding).where(MfHolding.scheme_slug.in_(scheme_slugs)))
        session.exec(delete(MfSectorAllocation).where(MfSectorAllocation.scheme_slug.in_(scheme_slugs)))
        session.exec(delete(MfAssetAllocation).where(MfAssetAllocation.scheme_slug.in_(scheme_slugs)))
        session.commit()

    progress = st.progress(0, text="Starting holdings updates...")
    success_count = 0

    for i, (name, slug) in enumerate(zip(scheme_names, scheme_slugs)):
        progress.progress(i / len(scheme_slugs), text=f"Fetching holdings: {name}")

        try:
            resp = fetch_portfolio_by_slug(slug)

            h = normalize_holdings(resp, slug)
            s = normalize_sector_allocation(resp, slug)
            a = normalize_asset_allocation(resp, slug)

            save_holdings(h)
            save_sectors(s)
            save_assets(a)

            st.success(f"**{name}** -- {h.height} holdings, {s.height} sectors, {a.height} asset types")
            success_count += 1
        except Exception as e:
            st.error(f"**{name}** ({slug}) -- Failed: {e}")
            with st.expander("Error details"):
                st.code(traceback.format_exc())

    progress.progress(1.0, text="Done!")
    load_holdings_data.clear()
    clear_freshness_cache()
    st.toast(f"Saved holdings data for {success_count}/{len(scheme_slugs)} funds")
    st.rerun()

# ==== AMFI Master Section ====
st.divider()
st.subheader("AMFI Master Data")

amfi_count = get_scheme_count()
st.write(f"**{amfi_count:,}** schemes in database")

if st.button("Sync AMFI Master", type="primary"):
    with st.spinner("Downloading AMFI NAVAll.txt..."):
        count = sync_amfi_master()
    st.success(f"Synced **{count:,}** schemes from AMFI")
    st.rerun()

# ==== Tradebook Section ====
st.divider()
st.subheader("Tradebook")

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

        # Auto-map ISINs if AMFI data is available
        if amfi_count > 0:
            with st.spinner("Auto-mapping ISINs to fund schemes..."):
                mappings = auto_map_tradebook()
            if mappings:
                st.success(f"Auto-mapped **{len(mappings)}** funds by ISIN")
                load_nav_data.clear()
            else:
                st.warning("Could not auto-map any funds. Sync AMFI data first.")
    elif skipped > 0:
        st.warning(f"All **{skipped}** trades already exist in the database.")
    else:
        st.error("CSV was empty or could not be parsed.")

if stats["total_trades"] > 0 and amfi_count > 0:
    if st.button("Re-map all tradebook ISINs"):
        with st.spinner("Auto-mapping ISINs..."):
            mappings = auto_map_tradebook()
        if mappings:
            st.success(f"Mapped **{len(mappings)}** funds")
            load_nav_data.clear()
        else:
            st.warning("No ISINs could be mapped.")

# ==== Database Statistics ====
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
