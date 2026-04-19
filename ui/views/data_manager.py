import streamlit as st
import polars as pl
import traceback
from datetime import datetime

from data.store.mutual_fund import (
    load_registry,
    save_to_registry,
    _fetch_single_nav,
    _load_scheme_code_map,
    _save_scheme_code_map,
    _save_nav_df,
    _load_nav_df,
    _load_holdings,
    _save_holdings,
    _save_sectors,
    _save_assets,
)
from data.fetchers.mutual_fund import fetch_portfolio_by_slug
from mutual_funds.holdings import (
    normalize_holdings,
    normalize_sector_allocation,
    normalize_asset_allocation,
)
from data.store.tradebook import import_tradebook_bytes, get_tradebook_stats
from data.store.amfi import sync_amfi_master, get_scheme_count
from data.store.mutual_fund import auto_map_tradebook
from ui.components.fund_picker import fund_picker
from ui.state.loaders import load_nav_data, load_holdings_data, load_txn_data
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

# ---- Load existing data from DB to show status
nav_df = _load_nav_df(scheme_names)
holdings_df = _load_holdings(scheme_slugs)

# ---- Build status table
st.subheader("NAV Data Status")

nav_status_rows = []
for name, slug in zip(scheme_names, scheme_slugs):
    scheme_nav = nav_df.filter(pl.col("schemeName") == name)
    if scheme_nav.height > 0:
        dates = scheme_nav.select("date").to_series()
        nav_status_rows.append(
            {
                "Fund": name,
                "Slug": slug,
                "Records": scheme_nav.height,
                "First Date": str(dates.min()),
                "Last Date": str(dates.max()),
                "Days Old": (datetime.today().date() - dates.max()).days,
            }
        )
    else:
        nav_status_rows.append(
            {
                "Fund": name,
                "Slug": slug,
                "Records": 0,
                "First Date": "-",
                "Last Date": "-",
                "Days Old": None,
            }
        )

nav_status_df = pl.DataFrame(nav_status_rows)
st.dataframe(
    nav_status_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Days Old": st.column_config.NumberColumn(
            help="Days since last available NAV data point"
        ),
    },
)

# ---- Holdings status
st.subheader("Holdings Data Status")

holdings_status_rows = []
for name, slug in zip(scheme_names, scheme_slugs):
    scheme_holdings = holdings_df.filter(pl.col("schemeSlug") == slug)
    holdings_status_rows.append(
        {
            "Fund": name,
            "Slug": slug,
            "Holdings Count": scheme_holdings.height,
        }
    )

holdings_status_df = pl.DataFrame(holdings_status_rows)
st.dataframe(holdings_status_df, use_container_width=True, hide_index=True)

# ---- Update controls
st.divider()
st.subheader("Update Data")

col1, col2, col3 = st.columns(3)
update_nav = col1.button("Update All NAV", type="primary", use_container_width=True)
update_holdings = col2.button(
    "Update All Holdings", type="secondary", use_container_width=True
)
update_all = col3.button(
    "Update Everything", type="secondary", use_container_width=True
)

if update_nav or update_all:
    st.markdown("#### NAV Update Progress")
    code_map = _load_scheme_code_map()

    # Delete existing NAV for selected schemes
    from core.database import get_session
    from core.models import MfNav, MfHolding, MfSectorAllocation, MfAssetAllocation
    from sqlmodel import delete

    with get_session() as session:
        session.execute(delete(MfNav).where(MfNav.scheme_name.in_(scheme_names)))
        session.commit()

    progress = st.progress(0, text="Starting NAV updates...")
    success_count = 0

    for i, name in enumerate(scheme_names):
        progress.progress(i / len(scheme_names), text=f"Fetching NAV: {name}")

        try:
            df = _fetch_single_nav(name, code_map)
            _save_nav_df(df)
            dates = df.select("date").to_series()
            st.success(
                f"**{name}** -- {df.height} records, {dates.min()} to {dates.max()}"
            )
            success_count += 1
        except Exception as e:
            st.error(f"**{name}** -- Failed: {e}")
            with st.expander("Error details"):
                st.code(traceback.format_exc())

    progress.progress(1.0, text="Done!")
    _save_scheme_code_map(code_map)
    load_nav_data.clear()
    st.toast(f"Saved NAV data for {success_count}/{len(scheme_names)} funds")


if update_holdings or update_all:
    st.markdown("#### Holdings Update Progress")

    # Delete existing holdings for selected slugs
    from core.database import get_session
    from core.models import MfHolding, MfSectorAllocation, MfAssetAllocation
    from sqlmodel import delete

    with get_session() as session:
        session.execute(delete(MfHolding).where(MfHolding.scheme_slug.in_(scheme_slugs)))
        session.execute(delete(MfSectorAllocation).where(MfSectorAllocation.scheme_slug.in_(scheme_slugs)))
        session.execute(delete(MfAssetAllocation).where(MfAssetAllocation.scheme_slug.in_(scheme_slugs)))
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

            _save_holdings(h)
            _save_sectors(s)
            _save_assets(a)

            st.success(
                f"**{name}** -- {h.height} holdings, "
                f"{s.height} sectors, {a.height} asset types"
            )
            success_count += 1
        except Exception as e:
            st.error(f"**{name}** ({slug}) -- Failed: {e}")
            with st.expander("Error details"):
                st.code(traceback.format_exc())

    progress.progress(1.0, text="Done!")
    load_holdings_data.clear()
    st.toast(f"Saved holdings data for {success_count}/{len(scheme_slugs)} funds")

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
