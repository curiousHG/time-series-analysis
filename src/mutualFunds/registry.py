import httpx
import polars as pl
from src.mutualFunds.constants import REGISTRY_PATH, MF_REGISTRY_URL


def load_registry() -> pl.DataFrame:
    if REGISTRY_PATH.exists():
        return pl.read_parquet(REGISTRY_PATH)

    return pl.DataFrame(
        schema={
            "schemeName": pl.Utf8,
            "source": pl.Utf8,
            "schemeSlug": pl.Utf8
        }
    )

def save_to_registry(names: list[str]):
    df = load_registry()

    slugs = ['-'.join([c.strip('-')for c in name.replace("("," ").replace(")"," ").split(' ') if c != '-' and c]) for name in names]
    
    new = pl.DataFrame(
        {
            "schemeName": names,
            "source": ["advisorkhoj"] * len(names),
            "schemeSlug": slugs
        }
    )

    df = (
        pl.concat([df, new])
        .unique(subset=["schemeName"])
        .sort("schemeName")
    )

    df.write_parquet(REGISTRY_PATH)


def fetch_scheme_registry(query:str) -> pl.DataFrame:
    """
    Fetch all mutual fund scheme codes and names from AMFI
    and add AdvisorKhoj-compatible slug
    """
    HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.advisorkhoj.com",
        "Referer": "https://www.advisorkhoj.com/mutual-funds-research/",
    }
    resp = httpx.post(MF_REGISTRY_URL, timeout=30, headers=HEADERS, data=f"query={query}")
    resp.raise_for_status()

    return resp.json()
