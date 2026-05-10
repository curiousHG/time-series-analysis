import contextlib
import datetime
import logging
import re
from urllib.parse import quote

import httpx
import polars as pl
from bs4 import BeautifulSoup

logger = logging.getLogger("data.fetchers.mutual_fund")

MFAPI_BASE_URL = "https://api.mfapi.in/mf"
AMFI_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
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
    words = [w for w in scheme_name.split() if len(w) > 1 and w.upper() not in ("-",)]
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


def fetch_fund_metadata(scheme_name: str) -> dict:
    """Scrape AdvisorKhoj fund overview page → metadata dict.

    Returns: {scheme_name, aum_crores, aum_as_of, expense_ratio, expense_ratio_as_of,
              benchmark, launch_date, category, asset_class, status, min_investment,
              min_topup, turnover_ratio, exit_load, fund_house, source_url}
    Fields not found are returned as None.
    """
    from datetime import datetime as dt

    html = fetch_fund_overview_html(scheme_name)
    soup = BeautifulSoup(html, "html.parser")
    url = BASE_OVERVIEW_URL.format(scheme_name=quote(scheme_name))

    out: dict = {
        "scheme_name": scheme_name,
        "aum_crores": None,
        "aum_as_of": None,
        "expense_ratio": None,
        "expense_ratio_as_of": None,
        "benchmark": None,
        "launch_date": None,
        "category": None,
        "asset_class": None,
        "status": None,
        "min_investment": None,
        "min_topup": None,
        "turnover_ratio": None,
        "exit_load": None,
        "fund_house": None,
        "source_url": url,
    }

    # Pull all label/value cells from the overview tables.
    cells: list[str] = []
    for table in soup.select("table.sch_over_table"):
        for td in table.select("td"):
            cells.append(td.get_text(" ", strip=True))

    def _find(prefix: str) -> str | None:
        for c in cells:
            if c.lower().startswith(prefix.lower()):
                return c.split(":", 1)[1].strip() if ":" in c else None
        return None

    def _parse_date(s: str | None) -> datetime.date | None:
        if not s:
            return None
        for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%d/%m/%Y"):
            try:
                return dt.strptime(s.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _parse_float(s: str | None) -> float | None:
        if not s:
            return None
        m = re.search(r"-?\d[\d,]*\.?\d*", s.replace(",", ""))
        return float(m.group()) if m else None

    out["category"] = _find("Category")
    out["asset_class"] = _find("Asset Class")
    out["benchmark"] = _find("Benchmark")
    out["status"] = _find("Status")
    out["launch_date"] = _parse_date(_find("Launch Date"))
    out["min_investment"] = _parse_float(_find("Minimum Investment"))
    out["min_topup"] = _parse_float(_find("Minimum Topup"))

    # TER: "0.62% As on (30-03-2026)"
    ter_raw = _find("TER")
    if ter_raw:
        out["expense_ratio"] = _parse_float(ter_raw)
        m = re.search(r"\((\d{2}-\d{2}-\d{4})\)", ter_raw)
        if m:
            out["expense_ratio_as_of"] = _parse_date(m.group(1))

    # Total Assets: "128,966.48 Cr As on 31-03-2026(Source:AMFI)"
    aum_raw = _find("Total Assets")
    if aum_raw:
        out["aum_crores"] = _parse_float(aum_raw)
        m = re.search(r"(\d{2}-\d{2}-\d{4})", aum_raw)
        if m:
            out["aum_as_of"] = _parse_date(m.group(1))

    # Turnover: "46.5%"
    turnover_raw = _find("Turn over") or _find("Turnover")
    if turnover_raw:
        out["turnover_ratio"] = _parse_float(turnover_raw)

    # Exit Load — capital-E "Exit Load:" is the structured field; lowercase
    # "exit load:" appears inline in the schedule text and shouldn't terminate the match.
    # Also skip the short "Exit Load: Yes/No/Nil" indicator if a longer one follows.
    joined = " ".join(cells)
    candidates = []
    for m in re.finditer(r"Exit Load\s*:\s*(.+?)(?=Exit Load\s*:|Turn ?over\s*:|$)", joined, re.DOTALL):
        cleaned = re.sub(r"\s+", " ", m.group(1)).strip()
        if cleaned:
            candidates.append(cleaned)
    if candidates:
        # Prefer the longest candidate; if all are short tokens like "Yes"/"Nil", keep that.
        out["exit_load"] = max(candidates, key=len)[:1000]

    # fund_house intentionally left None here — the repository layer fills it
    # from `amfi_schemes` (already populated by AMFI master sync).
    return out


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
            with contextlib.suppress(ValueError):
                nav = float(nav_str)

        nav_date = None
        if len(parts) > 5:
            date_str = parts[5].strip()
            with contextlib.suppress(ValueError):
                nav_date = dt.strptime(date_str, "%d-%b-%Y").date()

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
