"""Settings → Database Statistics section: total size + per-table rows / size."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services.db_stats import get_db_stats


def render() -> None:
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
