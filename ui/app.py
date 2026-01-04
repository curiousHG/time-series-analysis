import streamlit as st
from ui.sidebar import sidebar
from ui.views.backtest import render as render_backtest
from ui.views.mutualFund.main import render as render_mf

st.set_page_config(layout="wide")
# state = sidebar()

# if state["mode"] == "Stocks":
#     render_backtest(state)

# elif state["mode"] == "Mutual Funds":
#     render_mf(state)


pages = [
    st.Page("views/mutualFund/main.py", title="Mutual Fund Analysis"),
    st.Page("views/backtest.py", title = "Stock Analysis")
]
pg = st.navigation(pages, position='top')
pg.run()