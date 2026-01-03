import streamlit as st

def sidebar():
    st.sidebar.title("Controls")

    mode = st.sidebar.selectbox(
        "Mode",
        ["Stocks", "Mutual Funds"]
    )

    state = {"mode": mode}

    if mode == "Stocks":
        state.update(stock_controls())

    elif mode == "Mutual Funds":
        state.update(mf_controls())

    return state


def stock_controls():
    symbol = st.sidebar.selectbox("Symbol", ["reliance"])

    strategy = st.sidebar.selectbox(
        "Strategy",
        ["RSI", "MA Crossover"]
    )

    params = {}

    if strategy == "RSI":
        params["window"] = st.sidebar.slider(
            "RSI Window", 5, 30, 14
        )

    return {
        "symbol": symbol,
        "strategy": strategy,
        "params": params,
    }


def mf_controls():
    # scheme = st.sidebar.text_input("Scheme Code", "120503")
    # buy_date = st.sidebar.date_input("Buy Date")
    # amount = st.sidebar.number_input("Amount Invested", min_value=0)

    # return {
    #     "scheme_code": scheme,
    #     "buy_date": buy_date,
    #     "amount": amount,
    # }
    return {}
