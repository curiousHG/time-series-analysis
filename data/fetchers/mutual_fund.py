import logging
import re
from urllib.parse import quote, quote_plus

import httpx
import polars as pl
from bs4 import BeautifulSoup

logger = logging.getLogger("data.fetchers.mutual_fund")

MFAPI_BASE_URL = "https://api.mfapi.in/mf"
AMFI_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
MF_REGISTRY_URL = "https://www.advisorkhoj.com/mutual-funds-research/autoSuggestAllMfSchemes"
BASE_OVERVIEW_URL = "https://www.advisorkhoj.com/mutual-funds-research/{scheme_name}"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html",
}
NAV_URL = "https://www.advisorkhoj.com/mutual-funds-research/getCompleteNavReportForFundOverview"


def fetch_nav_from_mfapi(scheme_code: str, scheme_name: str) -> pl.DataFrame:
    """
    Fetch historical NAV for a mutual fund scheme from MFAPI.
    Returns DataFrame with columns: date, nav, schemeName
    """
    url = f"{MFAPI_BASE_URL}/{scheme_code}"
    logger.info("Fetching NAV from MFAPI: code=%s name=%s", scheme_code, scheme_name)

    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()

    raw = resp.json()
    data = raw.get("data", [])
    if not data:
        logger.warning("Empty NAV response from MFAPI for code=%s", scheme_code)
        raise ValueError(f"No NAV data from MFAPI for scheme code {scheme_code}")

    df = pl.DataFrame(data)
    result = df.with_columns(
        pl.col("date").str.strptime(pl.Date, "%d-%m-%Y"),
        pl.col("nav").cast(pl.Float64),
        pl.lit(scheme_name).alias("schemeName"),
    ).sort("date")
    logger.info("MFAPI NAV fetched: %d records for %s", result.height, scheme_name)
    return result


def search_mfapi(query: str) -> list[dict]:
    """
    Search MFAPI for schemes matching query.
    Returns list of {schemeCode, schemeName}.
    """
    if not query or len(query.strip()) < 2:
        return []

    resp = httpx.get(f"{MFAPI_BASE_URL}/search", params={"q": query}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _normalize_for_match(s: str) -> str:
    """Normalize scheme name for fuzzy matching."""
    return re.sub(r"[\s\-_]+", " ", s.strip().lower())


def resolve_mfapi_code(scheme_name: str) -> str | None:
    """
    Find the MFAPI scheme code that best matches the given scheme name.
    Uses first few keywords from the name to search, then picks the closest match.
    Returns scheme code as string, or None if no good match found.
    """
    # Use first 3-4 meaningful words for search
    words = [w for w in scheme_name.split() if len(w) > 1 and w.upper() not in ("-", "–")]
    search_query = " ".join(words[:4])

    results = search_mfapi(search_query)
    if not results:
        logger.debug("No MFAPI results for query=%s (scheme=%s)", search_query, scheme_name)
        return None

    target = _normalize_for_match(scheme_name)

    # Exact normalized match first
    for r in results:
        if _normalize_for_match(r["schemeName"]) == target:
            return str(r["schemeCode"])

    # Substring containment match
    for r in results:
        candidate = _normalize_for_match(r["schemeName"])
        if target in candidate or candidate in target:
            return str(r["schemeCode"])

    # Fall back to first result if search was specific enough (4+ words)
    if len(words) >= 4 and results:
        return str(results[0]["schemeCode"])

    return None


def fetch_portfolio_by_slug(slug: str):
    logger.info("Fetching portfolio from AdvisorKhoj: slug=%s", slug)
    body = f"scheme_amfi={slug}"

    URL = "https://www.advisorkhoj.com/mutual-funds-research/getPortfolioAnalysis"

    HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.advisorkhoj.com",
        "Referer": "https://www.advisorkhoj.com/mutual-funds-research/",
    }

    resp = httpx.post(
        URL,
        headers=HEADERS,
        data=body,
        timeout=20,
    )

    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"Empty portfolio response from AdvisorKhoj for slug '{slug}'")
    return data


def fetch_nav_from_advisorkhoj(
    scheme_name: str,
) -> dict:
    """
    Full pipeline:
    HTML → launch date → NAV JSON
    """
    html = fetch_fund_overview_html(scheme_name)
    launch_date = extract_launch_date(html)

    params = build_nav_params(scheme_name, launch_date)

    resp = httpx.get(
        NAV_URL,
        headers={
            **HEADERS,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.advisorkhoj.com/mutual-funds-research/",
        },
        params=params,
        timeout=30,
    )

    resp.raise_for_status()
    return {
        "scheme": scheme_name,
        "launch_date": launch_date,
        "nav_data": resp.json(),
    }


def search_advisorkhoj_schemes(query: str) -> pl.DataFrame:
    """
    Search AdvisorKhoj Scheme page and extract:
    - schemeName
    - schemeSlug

    Returns empty DataFrame if nothing found
    """
    if not query or len(query.strip()) < 2:
        return pl.DataFrame(
            schema={
                "schemeName": pl.Utf8,
                "schemeSlug": pl.Utf8,
            }
        )

    url = f"https://www.advisorkhoj.com/search?page=Scheme&keyword={quote_plus(query)}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120 Safari/537.36"
        )
    }

    resp = httpx.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []

    for a in soup.select("ul.padding-0 li a[href^='/mutual-funds-research/']"):
        name = a.get_text(strip=True)
        href = a.get("href", "").strip()

        if not name or not href:
            continue

        slug = href.replace("/mutual-funds-research/", "").strip("/")

        rows.append(
            {
                "schemeName": name,
                "schemeSlug": slug.lower(),
            }
        )

    if not rows:
        return pl.DataFrame(
            schema={
                "schemeName": pl.Utf8,
                "schemeSlug": pl.Utf8,
            }
        )

    return pl.DataFrame(rows).unique(subset=["schemeName"])


def fetch_fund_overview_html(scheme_name: str) -> str:
    """
    scheme_name example:
    'SBI ELSS Tax Saver FUND - REGULAR PLAN-GROWTH'
    """
    url = BASE_OVERVIEW_URL.format(scheme_name=quote(scheme_name))

    resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def extract_launch_date(html: str) -> str:
    """
    Returns date in DD-MM-YYYY format
    """
    soup = BeautifulSoup(html, "html.parser")

    for td in soup.select("table.sch_over_table td"):
        text = td.get_text(strip=True)
        if "Launch Date:" in text:
            # Example: "Launch Date: 31-03-1993"
            match = re.search(r"(\d{2}-\d{2}-\d{4})", text)
            if match:
                return match.group(1)

    raise ValueError(f"Launch Date not found on AdvisorKhoj for '{html[:80]}...' — this fund may not be supported")


def build_nav_params(scheme_name: str, launch_date: str) -> dict:
    """
    AdvisorKhoj expects scheme_amfi_name double-encoded
    """
    encoded = quote(quote(scheme_name.lower()))

    return {
        "scheme_amfi_name": encoded,
        "scheme_inception_date": launch_date,
    }


def fetch_scheme_registry(query: str) -> pl.DataFrame:
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


def fetch_amfi_master() -> list[dict]:
    """
    Download AMFI NAVAll.txt and parse into a list of scheme dicts.
    Format: SchemeCode;ISIN Payout/Growth;ISIN Reinvestment;SchemeName;NAV;Date
    Lines without semicolons are category/fund house headers.
    """
    from datetime import datetime as dt

    logger.info("Fetching AMFI master data from %s", AMFI_NAV_ALL_URL)
    resp = httpx.get(AMFI_NAV_ALL_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()

    schemes = []
    current_category = None
    current_fund_house = None

    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("Scheme Code"):
            continue

        parts = line.split(";")
        if len(parts) < 5:
            # Category or fund house header
            if "(" in line and ")" in line:
                current_category = line.strip()
            else:
                current_fund_house = line.strip()
            continue

        try:
            scheme_code = int(parts[0].strip())
        except ValueError:
            continue

        isin_growth = parts[1].strip() or None
        isin_reinvestment = parts[2].strip() if len(parts) > 2 else None
        if isin_reinvestment == "-":
            isin_reinvestment = None
        scheme_name = parts[3].strip()

        nav_str = parts[4].strip() if len(parts) > 4 else None
        nav = None
        if nav_str and nav_str not in ("N.A.", "-"):
            try:
                nav = float(nav_str)
            except ValueError:
                pass

        nav_date = None
        if len(parts) > 5:
            date_str = parts[5].strip()
            try:
                nav_date = dt.strptime(date_str, "%d-%b-%Y").date()
            except ValueError:
                pass

        schemes.append(
            {
                "scheme_code": scheme_code,
                "isin_growth": isin_growth,
                "isin_reinvestment": isin_reinvestment,
                "scheme_name": scheme_name,
                "nav": nav,
                "nav_date": nav_date,
                "fund_house": current_fund_house,
                "category": current_category,
            }
        )

    logger.info("Parsed %d schemes from AMFI master", len(schemes))
    return schemes
