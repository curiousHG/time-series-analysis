import yfinance as yf
import matplotlib.pyplot as plt
from mplfinance.original_flavor import candlestick_ohlc


STOCK = ['RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS']
data = yf.download(STOCK, period='1d', interval='15m')
