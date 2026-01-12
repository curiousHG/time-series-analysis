import polars as pl
import yfinance as yf


df = yf.download("AAPL", start="2022-01-01", end="2023-01-01", interval="1d")
# change from multi index to single index
"""
MultiIndex([(  'Date',     ''),
            ( 'Close', 'AAPL'),
            (  'High', 'AAPL'),
            (   'Low', 'AAPL'),
            (  'Open', 'AAPL'),
            ('Volume', 'AAPL')],

            change this to 
Index(['Date', 'Close', 'High', 'Low', 'Open', 'Volume'],
"""
df = df.reset_index()
df.columns = [col[0] for col in df.columns]
# set date as the index
df = df.set_index("Date")
pl_df = pl.from_pandas(df)
print(pl_df.columns)
