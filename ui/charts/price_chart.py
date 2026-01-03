import streamlit as st

def render_price_chart(fig):
    st.subheader("Price & Trades")
    st.plotly_chart(fig, width="stretch")
