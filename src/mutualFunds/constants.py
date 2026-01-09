from pathlib import Path


MF_REGISTRY_URL = "https://www.advisorkhoj.com/mutual-funds-research/autoSuggestAllMfSchemes"
RAW_DIR = Path("data/raw")
NAV_PATH = Path("data/parquet/mf_nav.parquet")
REGISTRY_PATH = Path("data/parquet/advisorkhoj_registry.parquet")
HOLDINGS_PATH = Path("data/parquet/mf_holdings.parquet")
SECTOR_PATH = Path("data/parquet/mf_sector_allocation.parquet")
ASSET_PATH = Path("data/parquet/mf_asset_allocation.parquet")

FUND_MAPPING_PATH = Path("data/user/fund_mapping.csv")


