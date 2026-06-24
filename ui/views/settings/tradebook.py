"""Settings → Portfolio Tradebook section: stats + CSV upload + live ISIN resolution."""

from __future__ import annotations

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_count
from data.repositories.tradebook import get_tradebook_stats, import_tradebook_bytes, load_tradebook_from_db
from ui.state.loaders import load_txn_data


def render() -> None:
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

    # ISIN → scheme preview; scheme_code/schemeName resolved at import via amfi_schemes
    # (matches both isin_growth and isin_reinvestment).
    if stats["total_trades"] > 0:
        tb = load_tradebook_from_db()
        if not tb.is_empty():
            resolved = (
                tb.select(["symbol", "isin", "schemeName", "scheme_code"])
                .rename({"schemeName": "scheme_name"})
                .unique()
            )
            matched = resolved.filter(pl.col("scheme_name").is_not_null()).height
            unmatched = resolved.height - matched
            c1, c2 = st.columns(2)
            c1.metric("Resolved by ISIN", matched)
            c2.metric("Unresolved", unmatched)
            with st.expander("ISIN → scheme resolution"):
                st.dataframe(resolved.to_pandas(), use_container_width=True, hide_index=True)
            if unmatched > 0 and get_scheme_count() == 0:
                st.warning("AMFI master is empty — sync below to enable ISIN resolution.")
