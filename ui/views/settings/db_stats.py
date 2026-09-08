"""Settings → Maintenance: database footprint per table."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services.db_stats import get_db_stats


def render() -> None:
    db_stats = get_db_stats()
    st.markdown(f"**Database**  \n`{db_stats.db_name}` · {db_stats.db_pretty} across {db_stats.table_count} tables.")
    table_stats_pd = pd.DataFrame(
        [{"Table": t.name, "Rows": t.rows, "Size": t.total_pretty, "bytes": t.total_bytes} for t in db_stats.tables]
    ).sort_values("bytes", ascending=False)
    st.dataframe(
        table_stats_pd.drop(columns="bytes"),
        use_container_width=True,
        hide_index=True,
        column_config={"Rows": st.column_config.NumberColumn(format="localized")},
    )
