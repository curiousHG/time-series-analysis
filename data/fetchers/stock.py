import yfinance as yf
import requests
import logging
import json
from pprint import pprint
import inspect

# logging.basicConfig(level=logging.DEBUG)


def query_stocks(query: str) -> dict:
    """Return list of stock symbols matching the query"""
    query = query.lower()

    # HEADERS = {
    #     "User-Agent": "PostmanRuntime/7.51.0",
    #     "Accept": "application/json",
    # }

    # YFINANCE_QUERY_URL = "https://query2.finance.yahoo.com/v1/finance/search"
    # params = {
    #     "q": query,
    #     "quotesCount": 50,
    # }
    # response = requests.get(
    #     YFINANCE_QUERY_URL, params=params, headers=HEADERS, timeout=10
    # )
    # return response.json()
    return yf.Lookup(query).all


def fetch_symbol_data(symbol: str, start: str, end: str, interval: str = "1d") -> yf.Ticker.history:
    """Fetch historical data for a given stock symbol"""
    try:
        data = yf.download(symbol, start=start, end=end, interval=interval, multi_level_index=False)
        # remove the symbol name in the columns make the columns only the field names
        if len(data) == 0:
            return None
    except Exception as e:
        print(f"Error fetching data for symbol {symbol}: {e}")
        return None
    return data



def dump_properties(obj):
    result = {}
    for name, attr in inspect.getmembers(type(obj)):
        if isinstance(attr, property):
            try:
                result[name] = getattr(obj, name)
            except Exception as e:
                result[name] = f"<error: {e}>"
    return result


def get_funds_data(ticker: yf.Ticker):
    """Fetch funds data from yfinance Ticker object"""
    funds_data: yf.FundsData = ticker.funds_data
    return dump_properties(funds_data)


def get_symbol_info(symbol: str):
    """Fetch symbol info from yfinance"""
    ticker = yf.Ticker(symbol)
    return ticker.info

