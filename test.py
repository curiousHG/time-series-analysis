from src.data.fetchers.mutual_fund import fetch_nav
from src.mutualFunds.registry import fetch_scheme_registry
import polars as pl


df = fetch_scheme_registry()
df.write_parquet("data/parquet/mf_registry.parquet")


data = pl.read_parquet("data/parquet/mf_registry.parquet")
# print(data)