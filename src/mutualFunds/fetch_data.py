import httpx
import polars as pl

BASE_URL = "https://api.mfapi.in/mf"

from urllib.parse import quote
from bs4 import BeautifulSoup
import re

BASE_OVERVIEW_URL = "https://www.advisorkhoj.com/mutual-funds-research/{scheme_name}"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html",
}


def fetch_fund_overview_html(scheme_name: str) -> str:
    """
    scheme_name example:
    'SBI ELSS Tax Saver FUND - REGULAR PLAN-GROWTH'
    """
    url = BASE_OVERVIEW_URL.format(scheme_name=quote(scheme_name))

    resp = httpx.get(url, headers=HEADERS, timeout=20,follow_redirects=True)
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

    raise ValueError("Launch Date not found")


def fetch_nav(scheme_code: str) -> pl.DataFrame:
    url = f"{BASE_URL}/{scheme_code}"

    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()

    raw = resp.json()

    df = pl.DataFrame(raw["data"])

    return df.with_columns(
        pl.col("date").str.strptime(pl.Date, "%d-%m-%Y"),
        pl.col("nav").cast(pl.Float64),
        pl.lit(scheme_code).alias("schemeCode"),
    ).sort("date")


NAV_URL = (
    "https://www.advisorkhoj.com/mutual-funds-research/"
    "getCompleteNavReportForFundOverview"
)


def build_nav_params(scheme_name: str, launch_date: str) -> dict:
    """
    AdvisorKhoj expects scheme_amfi_name double-encoded
    """
    encoded = quote(quote(scheme_name.lower()))

    return {
        "scheme_amfi_name": encoded,
        "scheme_inception_date": launch_date,
    }


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


def fetch_portfolio_by_slug(slug: str):
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
    return resp.json()
