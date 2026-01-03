from src.data.fetchers.mutual_fund import fetch_nav
from src.mf.registry import fetch_scheme_registry

# df = fetch_nav("120503")
# print(df.tail())

df = fetch_scheme_registry()
df.write_parquet("data/parquet/mf_registry.parquet")