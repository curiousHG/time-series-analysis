from sklearn.preprocessing import MinMaxScaler
import matplotlib.pyplot as plt
import time
import yfinance as yf
import pandas as pd
import polars as pl
import numpy as np
import itertools
from core.indicators import ATR, CO, MACD, MFI, momentum, normalized_ATR, OBV, HTS
from talib import abstract as ta
import plotly.express as px
import plotly.graph_objects as go
import httpx

x = [
    "RELIANCE.NS",
    "TATASTEEL.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "BAJAJ-AUTO.NS",
    "ICICIBANK.NS",
    "ITC.NS",
    "UPL.NS",
    "ONGC.NS",
    "HINDALCO.NS",
    "TITAN.NS",
    "COALINDIA.NS",
    "INDUSINDBK.NS",
    "BAJAJFINSV.NS",
    "GRASIM.NS",
    "JSWSTEEL.NS",
]

nifty50 = [
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "APOLLOHOSP.NS",
    "ASIANPAINT.NS",
    "AXISBANK.NS",
    "BAJAJ-AUTO.NS",
    "BAJFINANCE.NS",
    "BAJAJFINSV.NS",
    "BPCL.NS",
    "BHARTIARTL.NS",
    "BRITANNIA.NS",
    "CIPLA.NS",
    "COALINDIA.NS",
    "DIVISLAB.NS",
    "DRREDDY.NS",
    "EICHERMOT.NS",
    "GRASIM.NS",
    "HCLTECH.NS",
    "HDFCBANK.NS",
    "HDFCLIFE.NS",
    "HEROMOTOCO.NS",
    "HINDALCO.NS",
    "HINDUNILVR.NS",
    "HDFC.NS",
    "ICICIBANK.NS",
    "ITC.NS",
    "INDUSINDBK.NS",
    "INFY.NS",
    "JSWSTEEL.NS",
    "KOTAKBANK.NS",
    "LT.NS",
    "M&M.NS",
    "MARUTI.NS",
    "NTPC.NS",
    "NESTLEIND.NS",
    "ONGC.NS",
    "POWERGRID.NS",
    "RELIANCE.NS",
    "SBILIFE.NS",
    "SBIN.NS",
    "SUNPHARMA.NS",
    "TCS.NS",
    "TATACONSUM.NS",
    "TATAMOTORS.NS",
    "TATASTEEL.NS",
    "TECHM.NS",
    "TITAN.NS",
    "UPL.NS",
    "ULTRACEMCO.NS",
    "WIPRO.NS",
]

y = [
    "BTC-USD",
    "ETH-USD",
    "ADA-USD",
    "DOGE-USD",
    "SOL1-USD",
    "LTC-USD",
    "BNB-USD",
    "AVAX-USD",
    "UNI3-USD",
    "DOT1-USD",
    "SUSHI-USD",
    "LINK-USD",
    "XRP-USD",
    "ALGO-USD",
    "EOS-USD",
    "XTZ-USD",
    "FIL-USD",
    "ATOM1-USD",
    "TRX-USD",
    "MATIC-USD",
    "CHZ-USD",
    "FTT1-USD",
    "NEAR-USD",
    "SAND-USD",
    "WAVES-USD",
    "IOST-USD",
    "DASH-USD",
    "STORJ-USD",
    "ZEC-USD",
    "KSM-USD",
]

z = [
    "GC=F",
    "SI=F",
    "HG=F",
    "PL=F",
    "PA=F",
    "CL=F",
    "NG=F",
    "RB=F",
    "HO=F",
    "BZ=F",
    "KC=F",
    "SB=F",
    "CT=F",
    "HE=F",
    "LE=F",
    "GOLD",
]


data_stock = {}
timeframe = ["15m", "1h", "1d"]
# data_crypto = {}
# data_commodity = {}
# for stock in x:
#     # data_stock[stock] = yf.download(stock, period='2y', interval='1d')
#     # data_stock[stock].dropna(inplace=True).to_csv(f'./data/{stock}.csv')
#     for t in timeframe:
#         yf.download(stock, period='2y', interval=t).to_csv(f'./data/{stock}_{t}.csv')
#     print(stock)

# # rate of return over 2 days
# for stock in x:
#     data_stock[stock]['return_2d'] = (
#         data_stock[stock]['Close'] / data_stock[stock]['Open']) - 1

#     # new
# for stock in x:
#     data_stock[stock]['macd'], data_stock[stock]['signal'] = MACD(
#         data_stock[stock])

# for stock in x:
#     data_stock[stock]["atr"] = ATR(data_stock[stock])

#     data_stock[stock]["natr"] = normalized_ATR(data_stock[stock])
#     data_stock[stock]["momentum"] = momentum(data_stock[stock])
#     data_stock[stock]["co"] = CO(data_stock[stock])
#     data_stock[stock]["obv"] = OBV(data_stock[stock])
#     data_stock[stock]["mfi"] = MFI(data_stock[stock])
#     # data_stock[stock]["dcp"] = HTDCP(data_stock[stock])
#     data_stock[stock]["hts"] = HTS(data_stock[stock])
#     # data_stock[stock]["httmm"] = HTTMM(data_stock[stock])

# # movig avg 10 and 5 days
# for stock in x:
#     data_stock[stock]['ma_10d'] = data_stock[stock]['Close'].rolling(
#         window=10, min_periods=1).mean()
#     data_stock[stock]['ma_5d'] = data_stock[stock]['Close'].rolling(
#         window=5, min_periods=1).mean()

# # volatility
# for stock in x:
#     data_stock[stock]['volatility'] = np.log(
#         data_stock[stock]['Close'] / data_stock[stock]['Close'].shift(1)).rolling(window=10).std() * np.sqrt(252)
# # volume
# for stock in x:
#     data_stock[stock]['volume'] = data_stock[stock]['Volume']
# # rsi
# for stock in x:
#     delta = data_stock[stock]['Close'].diff()
#     gain = delta.where(delta > 0, 0)
#     loss = -delta.where(delta < 0, 0)
#     avg_gain = gain.rolling(window=14, min_periods=1).mean()
#     avg_loss = loss.rolling(window=14, min_periods=1).mean()
#     rs = avg_gain / avg_loss
#     data_stock[stock]['rsi'] = 100 - (100 / (1 + rs))

# # psychological index
# for stock in x:
#     n = 14
#     data_stock[stock]['up_days'] = data_stock[stock]['Close'] > data_stock[stock]['Close'].shift(
#         1)
#     data_stock[stock]['Nup'] = data_stock[stock]['up_days'].rolling(
#         window=n).sum()
#     data_stock[stock]['N'] = n
#     data_stock[stock]['psychological_index'] = data_stock[stock]['Nup'] / \
#         data_stock[stock]['N']
# for stock in x:
#     data_stock[stock]['return_prev_day'] = data_stock[stock]['return_2d'].shift(
#         1)
#     data_stock[stock]['return_prev_week'] = data_stock[stock]['return_2d'].shift(
#         7)
#     data_stock[stock]['return_prev_fortnight'] = data_stock[stock]['return_2d'].shift(
#         15)

# concatenate
# df_stock = pd.concat(data_stock, keys=x)
# save to csv
# df_stock.to_csv('data/stock_data.csv')

import requests


def query_stocks(query: str) -> list[str]:
    """Return list of stock symbols matching the query"""
    query = query.lower()

    HEADERS = {
        "User-Agent": "PostmanRuntime/7.51.0",
        "Accept": "application/json",
    }

    YFINANCE_QUERY_URL = "https://query2.finance.yahoo.com/v1/finance/search"
    params = {
        "q": query,
    }
    response = requests.get(
        YFINANCE_QUERY_URL, params=params, headers=HEADERS, timeout=10
    )
    return response.json()


# print(query_stocks("apple"))

def fetch_symbol_data(symbol: str):
    """Fetch historical data for a given stock symbol"""
    data = yf.download(symbol, period="5y", interval="1d")
    return data

def get_symbol_info(symbol: str):
    """Fetch symbol info from yfinance"""
    ticker = yf.Ticker(symbol)
    return ticker.info

print(fetch_symbol_data("0P00012ALR.BO"))
