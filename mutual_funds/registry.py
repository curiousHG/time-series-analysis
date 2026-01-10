import httpx
import polars as pl
from mutual_funds.constants import REGISTRY_PATH, MF_REGISTRY_URL


def make_slug(name: str) -> str:
    return "-".join(
        c.strip("-")
        for c in name.replace("(", " ").replace(")", " ").split()
        if c and c != "-"
    ).lower()

def load_registry() -> pl.DataFrame:
    if REGISTRY_PATH.exists():
        return pl.read_parquet(REGISTRY_PATH)

    return pl.DataFrame(
        schema={
            "schemeName": pl.Utf8,
            "schemeSlug": pl.Utf8,
            "source": pl.Utf8,
        }
    )

def save_to_registry(names: list[str]):
    if not names:
        return

    df = load_registry()
    
    new = pl.DataFrame(
        {
            "schemeName": names,
            "schemeSlug": [make_slug(n) for n in names],
            "source": ["advisorkhoj"] * len(names),
        }
    )
    # print(new, df)

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

    names: list[str] = resp.json()

    return pl.DataFrame({"schemeName": names})
