"""AdvisorKhoj: scheme portfolio, overview-page metadata and NAV history."""

from __future__ import annotations

import datetime
import logging
import re
from datetime import datetime as dt
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from data.constants import BASE_OVERVIEW_URL, HEADERS, NAV_URL
from data.exceptions import SourceNotFound, UpstreamFormatError
from data.fetchers._payload import shape_of

logger = logging.getLogger(__name__)


def fetch_portfolio(scheme_amfi: str):
    """POST AdvisorKhoj's portfolio endpoint for one scheme name (`scheme_amfi`) → parsed JSON.

    Raises `SourceNotFound` on a non-JSON answer and `UpstreamFormatError` on an unexpected envelope.
    """
    logger.debug("Fetching portfolio from AdvisorKhoj: scheme_amfi=%s", scheme_amfi)

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
        data={"scheme_amfi": scheme_amfi},
        timeout=20,
    )

    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        raise SourceNotFound("AdvisorKhoj portfolio", scheme_amfi) from e
    if not data:
        raise ValueError(f"Empty portfolio response from AdvisorKhoj for {scheme_amfi!r}")
    if not isinstance(data, dict) or "schemePortfolioAnalysisResponse" not in data:
        raise UpstreamFormatError(
            "AdvisorKhoj portfolio", f"expected a 'schemePortfolioAnalysisResponse' envelope, got {shape_of(data)}"
        )
    return data


def fetch_nav_from_advisorkhoj(
    scheme_name: str,
) -> dict:
    """AdvisorKhoj NAV pipeline: HTML → launch date → NAV JSON."""
    html = fetch_fund_overview_html(scheme_name)
    launch_date = extract_launch_date(html, scheme_name)

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


_EXIT_LOAD_FIELD_RE = re.compile(r"Exit Load\s*:\s*(.+?)(?=Exit Load\s*:|Turn ?over\s*:|$)", re.DOTALL)


def fetch_fund_metadata(scheme_name: str, *, lookup_key: str | None = None) -> dict:
    """Scrape the AdvisorKhoj overview page → metadata dict (missing fields = None).

    `lookup_key` overrides the URL key; the returned dict is still keyed on `scheme_name`.
    """
    key = lookup_key or scheme_name
    html = fetch_fund_overview_html(key)
    soup = BeautifulSoup(html, "html.parser")
    url = BASE_OVERVIEW_URL.format(scheme_name=quote(key))

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
        "investment_objective": None,
        "risk_level": None,
        "fund_house": None,
        "source_url": url,
    }

    for img in soup.find_all("img", alt="riskometer"):
        m = re.search(r"Riskometer-([\w-]+)\.png", img.get("src") or "")
        if m:
            out["risk_level"] = m.group(1).replace("-", " ")
            break

    for h in soup.select("h1, h2, h3, h4"):
        if "investment objective" in h.get_text(strip=True).lower():
            para = h.find_next("p")
            if para:
                text = re.sub(r"\s+", " ", para.get_text(" ", strip=True)).strip()
                out["investment_objective"] = text[:2000] or None
            break

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

    ter_raw = _find("TER")
    if ter_raw:
        out["expense_ratio"] = _parse_float(ter_raw)
        m = re.search(r"\((\d{2}-\d{2}-\d{4})\)", ter_raw)
        if m:
            out["expense_ratio_as_of"] = _parse_date(m.group(1))

    aum_raw = _find("Total Assets")
    if aum_raw:
        out["aum_crores"] = _parse_float(aum_raw)
        m = re.search(r"(\d{2}-\d{2}-\d{4})", aum_raw)
        if m:
            out["aum_as_of"] = _parse_date(m.group(1))

    turnover_raw = _find("Turn over") or _find("Turnover")
    if turnover_raw:
        out["turnover_ratio"] = _parse_float(turnover_raw)

    joined = " ".join(cells)
    candidates = []
    for m in _EXIT_LOAD_FIELD_RE.finditer(joined):
        cleaned = re.sub(r"\s+", " ", m.group(1)).strip()
        if cleaned:
            candidates.append(cleaned)
    if candidates:
        out["exit_load"] = max(candidates, key=len)[:1000]

    return out


def fetch_fund_overview_html(scheme_name: str) -> str:
    """Raw AdvisorKhoj overview HTML for a scheme name; raises `SourceNotFound` when the page is the homepage."""
    scheme_name = re.sub(r"\s+", " ", scheme_name).strip()
    url = BASE_OVERVIEW_URL.format(scheme_name=quote(scheme_name))

    resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    if "sch_over_table" not in resp.text:
        raise SourceNotFound("AdvisorKhoj overview", scheme_name)
    return resp.text


def extract_launch_date(html: str, scheme_name: str) -> str:
    """Launch date as DD-MM-YYYY; raises `SourceNotFound` when the page carries none."""
    soup = BeautifulSoup(html, "html.parser")

    for td in soup.select("table.sch_over_table td"):
        text = td.get_text(strip=True)
        if "Launch Date:" in text:
            match = re.search(r"(\d{2}-\d{2}-\d{4})", text)
            if match:
                return match.group(1)

    raise SourceNotFound("AdvisorKhoj launch date", scheme_name)


def build_nav_params(scheme_name: str, launch_date: str) -> dict:
    """Build NAV params; AdvisorKhoj expects scheme_amfi_name double-encoded."""
    encoded = quote(quote(scheme_name.lower()))

    return {
        "scheme_amfi_name": encoded,
        "scheme_inception_date": launch_date,
    }
