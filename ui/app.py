import streamlit as st


st.set_page_config(layout="wide")

pages = [
    st.Page("views/mutualFund/main.py", title="Mutual Fund Analysis"),
    st.Page("views/backtest.py", title = "Stock Analysis")
]
pg = st.navigation(pages, position='top')
pg.run()