import streamlit as st


def run():
    st.set_page_config(layout="wide")
    # st.write("🚀 App started")
    pages = [
        st.Page("ui/views/mutualFund.py", title="Mutual Fund Analysis"),
        st.Page("ui/views/backtest.py", title = "Stock Analysis")
    ]
    pg = st.navigation(pages, position='top')
    pg.run()