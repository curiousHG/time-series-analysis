"""screener.in scraper — HTTP only, no DB (per the fetcher/repository split).

Public (no login):
  - `search_company(q)`  → company search JSON (id, name, url)
  - `fetch_company(sym)` → parsed company page: top ratios + quarters / P&L / balance
    sheet / cash flow / ratios / shareholding tables

Authenticated (needs a logged-in `sessionid` cookie — set SCREENER_SESSIONID):
  - `run_screen(query, ...)` → the /screen/raw/ results table as rows

Be polite: keep the request rate low; callers cache results in the DB (DB-first policy).
The screen endpoint and Excel export are login-gated; company pages and search are public.
"""

from __future__ import annotations

import logging
import os
import re

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("data.fetchers.screener_in")

BASE_URL = "https://www.screener.in"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/json,application/xhtml+xml",
}
# NSE symbol → screener.in slug, for names that differ (corporate-action renames etc.).
_SLUG_OVERRIDES = {"TATAMOTORS": "TMCV"}
# Company-page section ids → the structured key we expose.
_SECTIONS = {
    "quarters": "quarters",
    "profit-loss": "profit_loss",
    "balance-sheet": "balance_sheet",
    "cash-flow": "cash_flow",
    "ratios": "ratios",
    "shareholding": "shareholding",
}


def _client(*, cookie: str | None = None) -> httpx.Client:
    cookies = {"sessionid": cookie} if cookie else None
    return httpx.Client(base_url=BASE_URL, headers=HEADERS, cookies=cookies, timeout=30, follow_redirects=True)


def _clean_label(text: str) -> str:
    """Row labels carry a trailing '+' (expandable) — strip it and whitespace."""
    return re.sub(r"\s+", " ", text).strip().rstrip("+").strip()


def _num(text: str | None) -> float | None:
    """Parse a screener cell ('₹ 17,77,905 Cr.', '22.8', '15%', '-66', '') → float or None."""
    if not text:
        return None
    t = text.replace(",", "").replace("₹", "").replace("%", "").replace("Cr.", "").strip()
    m = re.search(r"-?\d+\.?\d*", t)
    return float(m.group()) if m else None


def search_company(query: str) -> list[dict]:
    """Public company search → [{id, name, url}, ...]."""
    with _client() as c:
        r = c.get("/api/company/search/", params={"q": query})
        r.raise_for_status()
        return r.json()


def _parse_table(section) -> dict[str, dict[str, float | None]]:
    """A company-page section table → {row_label: {period: value}}."""
    table = section.find("table")
    if not table:
        return {}
    periods = [th.get_text(strip=True) for th in table.select("thead th")][1:]  # drop the empty corner cell
    out: dict[str, dict[str, float | None]] = {}
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        label = _clean_label(cells[0].get_text(strip=True))
        if not label:
            continue
        out[label] = {periods[i]: _num(td.get_text(strip=True)) for i, td in enumerate(cells[1:]) if i < len(periods)}
    return out


def fetch_company(symbol: str, *, consolidated: bool = True) -> dict | None:
    """Scrape a company's fundamentals page into a structured dict (None if not found).

    Tries the consolidated page first, falling back to standalone (some companies have only
    standalone). Returns top ratios + the quarter/annual/shareholding tables.
    """
    slug = _SLUG_OVERRIDES.get(symbol, symbol)
    paths = [f"/company/{slug}/consolidated/", f"/company/{slug}/"]
    if not consolidated:
        paths.reverse()
    with _client() as c:
        html = None
        for path in paths:
            r = c.get(path)
            if r.status_code == 200 and "/company/" in str(r.url):
                html = r.text
                break
        if html is None:
            logger.warning("screener.in: no company page for %s", symbol)
            return None

    soup = BeautifulSoup(html, "html.parser")
    name_el = soup.select_one("h1")
    top_ratios: dict[str, float | None] = {}
    for li in soup.select("#top-ratios li"):
        name_li, val_li = li.select_one(".name"), li.select_one(".value")
        if name_li and val_li:
            top_ratios[_clean_label(name_li.get_text(strip=True))] = _num(val_li.get_text(" ", strip=True))
    data = {
        "symbol": symbol,
        "name": name_el.get_text(strip=True) if name_el else symbol,
        "top_ratios": top_ratios,
    }
    for sec_id, key in _SECTIONS.items():
        sec = soup.find(id=sec_id)
        data[key] = _parse_table(sec) if sec else {}
    return data


def run_screen(
    query: str,
    *,
    source_id: str | int | None = None,
    sort: str = "",
    order: str = "",
    page: int = 1,
    cookie: str | None = None,
) -> list[dict]:
    """Run a /screen/raw/ query (REQUIRES a logged-in session cookie) → list of row dicts.

    Pass `cookie` (the `sessionid` value) or set SCREENER_SESSIONID. Raises if not logged in.
    """
    cookie = cookie or os.environ.get("SCREENER_SESSIONID")
    if not cookie:
        raise RuntimeError("run_screen needs a logged-in session: set SCREENER_SESSIONID or pass cookie=")
    params = {"query": query, "sort": sort, "order": order, "page": page}
    if source_id is not None:
        params["source_id"] = source_id
    with _client(cookie=cookie) as c:
        r = c.get("/screen/raw/", params=params)
        if any(x in str(r.url) for x in ("/login", "/register")):
            raise RuntimeError("screener.in rejected the session (expired SCREENER_SESSIONID?)")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

    table = soup.find("table")
    if not table:
        return []
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    rows: list[dict] = []
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) >= len(headers) and cells[0]:
            rows.append(dict(zip(headers, cells, strict=False)))
    return rows
