"""Settings → Portfolio: the tradebook in one sentence, upload behind a popover, and the ISIN
resolution table only when something is unresolved."""

from __future__ import annotations

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_count
from data.repositories.tradebook import get_tradebook_stats, import_tradebook_bytes, load_tradebook_from_db
from ui.components.chrome import section_header
from ui.state.loaders import load_txn_data
from ui.views.settings.format import plural


def _upload() -> None:
    uploaded = st.file_uploader("Kite/Zerodha tradebook CSV", type=["csv"], key="tradebook_upload")
    if uploaded is None:
        return
    with st.spinner("Importing tradebook…"):
        new_count, skipped = import_tradebook_bytes(uploaded.getvalue())
    if new_count > 0:
        st.success(f"Imported {plural(new_count, 'new trade')}, skipped {plural(skipped, 'duplicate')}.")
        load_txn_data.clear()
    elif skipped > 0:
        st.warning(f"All {skipped} trades were already imported.")
    else:
        st.error("The CSV was empty or could not be parsed.")


def render() -> None:
    actions = section_header("Portfolio")
    with actions, st.popover("Upload tradebook CSV", use_container_width=True):
        _upload()

    stats = get_tradebook_stats()
    if stats["total_trades"] == 0:
        st.markdown("No trades yet. Upload your Kite or Zerodha tradebook to unlock the Portfolio page.")
        return

    tb = load_tradebook_from_db()
    resolved = tb.select(["symbol", "isin", "schemeName", "scheme_code"]).rename({"schemeName": "scheme_name"}).unique()
    unmatched = resolved.filter(pl.col("scheme_name").is_null())
    first, last = stats["first_date"], stats["last_date"]
    st.markdown(
        f"{plural(stats['total_trades'], 'trade')} across {plural(stats['symbols'], 'fund')}, "
        f"{first:%b %Y} to {last:%b %Y} ({stats['buys']} buys, {stats['sells']} sells). "
        + (
            "All ISINs resolved to AMFI schemes."
            if unmatched.is_empty()
            else f"{plural(unmatched.height, 'ISIN')} unresolved."
        )
    )
    if not unmatched.is_empty():
        if get_scheme_count() == 0:
            st.warning("The AMFI master is empty. Sync it below so ISINs can resolve.")
        with st.expander("Unresolved ISINs"):
            st.dataframe(unmatched.to_pandas(), use_container_width=True, hide_index=True)
