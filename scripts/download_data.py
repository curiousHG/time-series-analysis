import yfinance as yf
import polars as pl


def fetch(symbol="RELIANCE.NS"):
    df = yf.download(symbol, period="2y", interval="1d", group_by="column")

    # Flatten columns (THIS IS THE FIX)
    df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    return pl.from_pandas(df.reset_index())


df = fetch()
df.write_parquet("data/parquet/reliance.parquet")
