import contextlib
import datetime
import logging
import re
from datetime import datetime as dt
from urllib.parse import quote

import httpx
import polars as pl
from bs4 import BeautifulSoup

from data.constants import AMFI_NAV_ALL_URL, BASE_OVERVIEW_URL, HEADERS, MFAPI_BASE_URL, NAV_URL

logger = logging.getLogger("data.fetchers.mutual_fund")


def fetch_nav_from_mfapi(scheme_code: str, scheme_name: str) -> pl.DataFrame:
    """Fetch historical NAV from MFAPI → (date, nav, schemeName) DataFrame."""
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
    """Search MFAPI for schemes → list of {schemeCode, schemeName}."""
    if not query or len(query.strip()) < 2:
        return []

    resp = httpx.get(f"{MFAPI_BASE_URL}/search", params={"q": query}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _normalize_for_match(s: str) -> str:
    """Normalize scheme name for fuzzy matching."""
    return re.sub(r"[\s\-_]+", " ", s.strip().lower())


def resolve_mfapi_code(scheme_name: str) -> str | None:
    """Find the MFAPI scheme code best matching scheme_name (str), or None."""
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
    """AdvisorKhoj NAV pipeline: HTML → launch date → NAV JSON."""
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
    """Scrape AdvisorKhoj fund overview page → metadata dict (missing fields = None)."""
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
    """Fetch raw overview HTML. scheme_name e.g. 'SBI ELSS Tax Saver FUND - REGULAR PLAN-GROWTH'."""
    url = BASE_OVERVIEW_URL.format(scheme_name=quote(scheme_name))

    resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def extract_launch_date(html: str) -> str:
    """Returns launch date as DD-MM-YYYY."""
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
    """Build NAV params; AdvisorKhoj expects scheme_amfi_name double-encoded."""
    encoded = quote(quote(scheme_name.lower()))

    return {
        "scheme_amfi_name": encoded,
        "scheme_inception_date": launch_date,
    }


# AMFI scheme-type headers, e.g. "Open Ended Schemes(Equity Scheme - Large Cap Fund)".
_AMFI_CATEGORY_RE = re.compile(
    r"^\s*(?:Open Ended|Close Ended|Interval Fund)\s+Schemes?\s*\((?P<inner>.+)\)\s*$",
    re.IGNORECASE,
)
# Legacy/close-ended headers without a "<Class> Scheme - <Sub>" split (e.g. "Income", "Growth").
_LEGACY_CLASS = {
    "income": "Debt",
    "debt": "Debt",
    "liquid": "Debt",
    "gilt": "Debt",
    "money market": "Debt",
    "growth": "Equity",
    "equity": "Equity",
    "elss": "Equity",
    "balanced": "Hybrid",
}


def _classify_amfi_header(line: str) -> tuple[str | None, str | None, str | None]:
    """Classify a non-data AMFI line as (asset_class, sub_category, fund_house).

    Category headers are SEBI scheme-type lines (`Open/Close Ended Schemes(<Class> Scheme - <Sub>)`)
    → asset_class ("Equity"/"Debt"/"Hybrid"/"Other"/"Solution Oriented") + sub_category
    ("Large Cap Fund"). Legacy headers without the " - " split fall back via `_LEGACY_CLASS`.
    Everything else is an AMC/fund-house line — these can also contain parentheses (e.g.
    "IL&FS Mutual Fund (IDF)"), which the old paren-only heuristic mis-filed as a category.
    """
    m = _AMFI_CATEGORY_RE.match(line)
    if not m:
        return None, None, line.strip()
    inner = re.sub(r"\s+", " ", m.group("inner")).strip()
    if " - " in inner:
        class_part, sub = inner.split(" - ", 1)
        asset_class = class_part.replace("Scheme", "").strip() or "Other"
        return asset_class, sub.strip(), None
    return _LEGACY_CLASS.get(inner.lower(), "Other"), inner, None


def fetch_amfi_master() -> list[dict]:
    """Download AMFI NAVAll.txt → list of scheme dicts.

    Line format: SchemeCode;ISIN Payout/Growth;ISIN Reinvestment;SchemeName;NAV;Date.
    Lines without semicolons are scheme-type (category) or fund-house headers.
    """
    logger.info("Fetching AMFI master data from %s", AMFI_NAV_ALL_URL)
    resp = httpx.get(AMFI_NAV_ALL_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()

    schemes = []
    current_category = None
    current_sub_category = None
    current_fund_house = None

    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("Scheme Code"):
            continue

        parts = line.split(";")
        if len(parts) < 5:
            # Scheme-type (asset class + sub-category) or fund-house header.
            asset_class, sub_category, fund_house = _classify_amfi_header(line)
            if asset_class is not None:
                current_category = asset_class
                current_sub_category = sub_category
            else:
                current_fund_house = fund_house
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
                "sub_category": current_sub_category,
            }
        )

    logger.info("Parsed %d schemes from AMFI master", len(schemes))
    return schemes
