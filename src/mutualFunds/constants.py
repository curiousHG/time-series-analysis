from pathlib import Path


MF_REGISTRY_URL = "https://www.advisorkhoj.com/mutual-funds-research/autoSuggestAllMfSchemes"
NAV_PATH = Path("data/parquet/mf_nav.parquet")
REGISTRY_PATH = Path("data/parquet/mf_registry.parquet")
HOLDINGS_PATH = Path("data/parquet/mf_holdings.parquet")
SECTOR_PATH = Path("data/parquet/mf_sector_allocation.parquet")
ASSET_PATH = Path("data/parquet/mf_asset_allocation.parquet")