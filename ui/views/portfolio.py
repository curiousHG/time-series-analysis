"""Portfolio page — fund allocation, invested over time, growth comparison."""

import streamlit as st

from ui.state.loaders import load_txn_data
from ui.views.mf_tabs import portfolio


st.title("Portfolio")

txn_df = load_txn_data()
portfolio.render(txn_df)
