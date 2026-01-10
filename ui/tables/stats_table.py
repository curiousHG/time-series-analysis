import streamlit as st

def render_stats(pf):
    st.subheader("Performance Stats")
    st.dataframe(pf.stats())
