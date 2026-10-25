import contextlib
import datetime
import logging
import re
import threading
import time
from datetime import datetime as dt
from urllib.parse import quote

import httpx
import polars as pl
from bs4 import BeautifulSoup

from data.constants import AMFI_NAV_ALL_URL, BASE_OVERVIEW_URL, HEADERS, MFAPI_BASE_URL, NAV_URL
from data.exceptions import SourceNotFound, SourceThrottled, UpstreamFormatError

logger = logging.getLogger("data.fetchers.mutual_fund")


def _shape_of(payload: object) -> str:
    """Short description of an unexpected payload, for `UpstreamFormatError` messages."""
    if isinstance(payload, dict):
        return f"object with keys {sorted(payload)[:8]}"
    if isinstance(payload, list):
        return f"list of {len(payload)}"
    return type(payload).__name__


_MFAPI_BACKOFF_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)


class _Cooldown:
    """A pause shared by every thread calling one source."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resume_at = 0.0

    def wait(self) -> None:
        with self._lock:
            delay = self._resume_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def hold(self, seconds: float) -> None:
        with self._lock:
            self._resume_at = max(self._resume_at, time.monotonic() + seconds)


_MFAPI_COOLDOWN = _Cooldown()


def _get_mfapi(url: str) -> httpx.Response:
    """GET from MFAPI, waiting out 429s; raises `SourceThrottled` once the backoff is exhausted."""
    for backoff in (*_MFAPI_BACKOFF_SECONDS, None):
        _MFAPI_COOLDOWN.wait()
        resp = httpx.get(url, timeout=30)
        if resp.status_code != 429:
            return resp
        if backoff is None:
            break
        logger.info("MFAPI returned 429 for %s — pausing all MFAPI requests %.0fs", url, backoff)
        _MFAPI_COOLDOWN.hold(backoff)
    raise SourceThrottled("MFAPI", len(_MFAPI_BACKOFF_SECONDS) + 1)


def fetch_nav_from_mfapi(scheme_code: str, scheme_name: str) -> pl.DataFrame:
    """Fetch historical NAV from MFAPI → (date, nav, schemeName) DataFrame."""
    url = f"{MFAPI_BASE_URL}/{scheme_code}"
    logger.debug("Fetching NAV from MFAPI: code=%s name=%s", scheme_code, scheme_name)

    resp = _get_mfapi(url)
    resp.raise_for_status()

    raw = resp.json()
    if not isinstance(raw, dict) or "data" not in raw:
        raise UpstreamFormatError("MFAPI", f"expected an object with a 'data' key, got {_shape_of(raw)}")
    data = raw["data"]
    if not data:
        logger.debug("Empty NAV response from MFAPI for code=%s", scheme_code)
        raise ValueError(f"No NAV data from MFAPI for scheme code {scheme_code}")
    missing = {"date", "nav"} - set(data[0])
    if missing:
        raise UpstreamFormatError("MFAPI", f"NAV rows lost the {sorted(missing)} key(s); got {sorted(data[0])}")

    df = pl.DataFrame(data)
    result = df.with_columns(
        pl.col("date").str.strptime(pl.Date, "%d-%m-%Y"),
        pl.col("nav").cast(pl.Float64),
        pl.lit(scheme_name).alias("schemeName"),
    ).sort("date")
    logger.debug("MFAPI NAV fetched: %d records for %s", result.height, scheme_name)
    return result


def search_mfapi(query: str) -> list[dict]:
    """Search MFAPI for schemes → list of {schemeCode, schemeName}."""
    if not query or len(query.strip()) < 2:
        return []

    resp = httpx.get(f"{MFAPI_BASE_URL}/search", params={"q": query}, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not isinstance(results, list):
        raise UpstreamFormatError("MFAPI search", f"expected a list of schemes, got {_shape_of(results)}")
    if results and not {"schemeCode", "schemeName"} <= set(results[0]):
        raise UpstreamFormatError("MFAPI search", f"result rows lost schemeCode/schemeName; got {sorted(results[0])}")
    return results


def _normalize_for_match(s: str) -> str:
    """Normalize scheme name for fuzzy matching."""
    return re.sub(r"[\s\-_]+", " ", s.strip().lower())


def resolve_mfapi_code(scheme_name: str) -> str | None:
    """Find the MFAPI scheme code best matching scheme_name (str), or None."""
    words = [w for w in scheme_name.split() if len(w) > 1 and w.upper() not in ("-",)]
    search_query = " ".join(words[:4])

    results = search_mfapi(search_query)
    if not results:
        logger.debug("No MFAPI results for query=%s (scheme=%s)", search_query, scheme_name)
        return None

    target = _normalize_for_match(scheme_name)

    for r in results:
        if _normalize_for_match(r["schemeName"]) == target:
            return str(r["schemeCode"])

    for r in results:
        candidate = _normalize_for_match(r["schemeName"])
        if target in candidate or candidate in target:
            return str(r["schemeCode"])

    if len(words) >= 4 and results:
        return str(results[0]["schemeCode"])

    return None


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
            "AdvisorKhoj portfolio", f"expected a 'schemePortfolioAnalysisResponse' envelope, got {_shape_of(data)}"
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


_AMFI_CATEGORY_RE = re.compile(
    r"^\s*(?:Open Ended|Close Ended|Interval Fund)\s+Schemes?\s*\((?P<inner>.+)\)\s*$",
    re.IGNORECASE,
)

_ASSET_CLASSES = frozenset({"Equity", "Debt", "Hybrid", "Solution Oriented", "Index", "ETF", "Fund of Funds", "Other"})
_ASSET_CLASS_ALIASES = {
    "equity": "Equity",
    "growth": "Equity",
    "elss": "Equity",
    "debt": "Debt",
    "income": "Debt",
    "income/debt oriented": "Debt",
    "liquid": "Debt",
    "gilt": "Debt",
    "money market": "Debt",
    "hybrid": "Hybrid",
    "balanced": "Hybrid",
    "solution oriented": "Solution Oriented",
    "children's fund": "Solution Oriented",
    "childrens' fund": "Solution Oriented",
    "childrens fund": "Solution Oriented",
    "retirement fund": "Solution Oriented",
    "life cycle funds": "Solution Oriented",
    "index funds": "Index",
    "index": "Index",
    "exchange traded funds (etfs)": "ETF",
    "etfs": "ETF",
    "etf": "ETF",
    "fund of funds (domestic)": "Fund of Funds",
    "overseas fund of funds": "Fund of Funds",
    "fund of funds": "Fund of Funds",
    "other": "Other",
}

_SUB_CLASS_ALIASES = {
    "index funds": "Index",
    "gold etf": "ETF",
    "other etfs": "ETF",
    "fof domestic": "Fund of Funds",
    "fof overseas": "Fund of Funds",
}


def _normalise_class(text: str) -> str:
    """Collapse an AMFI class label to its bare form (no footnote asterisks, "Scheme(s)" filler or curly quotes)."""
    s = text.replace("’", "'")  # noqa: RUF001
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\*+$", "", s)
    s = re.sub(r"\s+Schemes?\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canonical_asset_class(text: str) -> str:
    """Map an AMFI class label onto `_ASSET_CLASSES`; unknown labels are logged and filed as "Other"."""
    bare = _normalise_class(text)
    if not bare:
        return "Other"
    canonical = _ASSET_CLASS_ALIASES.get(bare.lower())
    if canonical is None:
        logger.warning("Unmapped AMFI asset class %r — filed as 'Other'", bare)
        return "Other"
    return canonical


def _classify_amfi_header(line: str) -> tuple[str | None, str | None, str | None]:
    """Classify a non-data AMFI line as (asset_class, sub_category, fund_house); the unused fields are None."""
    m = _AMFI_CATEGORY_RE.match(line)
    if not m:
        return None, None, line.strip()
    inner = re.sub(r"\s+", " ", m.group("inner")).strip()
    if " - " in inner:
        class_part, sub = inner.split(" - ", 1)
        sub = _normalise_class(sub)
        asset_class = _canonical_asset_class(class_part)
        if asset_class == "Other":
            asset_class = _SUB_CLASS_ALIASES.get(sub.lower(), "Other")
        return asset_class, sub, None
    return _canonical_asset_class(inner), _normalise_class(inner), None


_POSITIONAL_LAYOUTS = {
    8: {
        "scheme_code": 0,
        "isin_growth": 1,
        "isin_reinvestment": 2,
        "scheme_name": 3,
        "plan": 4,
        "option": 5,
        "nav": 6,
        "nav_date": 7,
    },
    6: {"scheme_code": 0, "isin_growth": 1, "isin_reinvestment": 2, "scheme_name": 3, "nav": 4, "nav_date": 5},
}
_MIN_DATA_FIELDS = min(_POSITIONAL_LAYOUTS)


def _header_field(cell: str) -> str | None:
    """Map one NAVAll.txt header cell to a field name (None when unrecognised)."""
    c = re.sub(r"\s+", " ", cell).strip().lower()
    if "scheme code" in c:
        return "scheme_code"
    if "isin" in c:
        return "isin_reinvestment" if "reinvest" in c else "isin_growth"
    if "scheme name" in c:
        return "scheme_name"
    if c == "plan":
        return "plan"
    if c == "option":
        return "option"
    if "net asset value" in c or c == "nav":
        return "nav"
    if "date" in c:
        return "nav_date"
    return None


def _parse_header(line: str) -> dict[str, int] | None:
    """Build a {field: column index} map from the header row, or None if it doesn't parse."""
    layout: dict[str, int] = {}
    for i, cell in enumerate(line.split(";")):
        field = _header_field(cell)
        if field and field not in layout:
            layout[field] = i
    return layout if {"scheme_code", "scheme_name", "nav"} <= layout.keys() else None


def _layout_for(parts: list[str], header_layout: dict[str, int] | None) -> dict[str, int] | None:
    """Column map for one data row: the header layout if it fits, else the widest positional layout."""
    if header_layout and max(header_layout.values()) < len(parts):
        return header_layout
    for width in sorted(_POSITIONAL_LAYOUTS, reverse=True):
        if len(parts) >= width:
            return _POSITIONAL_LAYOUTS[width]
    return None


def _cell(parts: list[str], layout: dict[str, int], field: str) -> str | None:
    idx = layout.get(field)
    if idx is None:
        return None
    value = parts[idx].strip()
    return value or None


def compose_scheme_name(base: str, plan: str | None, option: str | None) -> str:
    """Full scheme name, "<base> - <plan> - <option>"; blank plan/option parts are dropped."""
    return " - ".join(p for p in (base, plan, option) if p)


_AMFI_MIN_SCHEMES = 1_000
_AMFI_MIN_VALUE_COVERAGE = 0.5


def _validate_amfi(schemes: list[dict], *, header_seen: bool, header_layout: dict[str, int] | None) -> None:
    """Raise `UpstreamFormatError` when the parsed rows no longer look like NAVAll.txt."""
    names = [row["scheme_name"] for row in schemes]
    if len(names) != len(set(names)):
        raise UpstreamFormatError("AMFI master", "scheme names are not unique after disambiguation")
    if header_seen and header_layout is None:
        raise UpstreamFormatError("AMFI NAVAll.txt", "header row present but no recognisable columns in it")
    if not header_seen:
        logger.warning("AMFI NAVAll.txt had no header row — falling back to positional columns")

    if len(schemes) < _AMFI_MIN_SCHEMES:
        raise UpstreamFormatError(
            "AMFI NAVAll.txt", f"only {len(schemes)} schemes parsed, expected >= {_AMFI_MIN_SCHEMES}"
        )

    total = len(schemes)
    for field in ("nav", "nav_date"):
        coverage = sum(row[field] is not None for row in schemes) / total
        if coverage < _AMFI_MIN_VALUE_COVERAGE:
            raise UpstreamFormatError(
                "AMFI NAVAll.txt",
                f"only {coverage:.1%} of {total} rows have a {field} — the column has probably moved",
            )
    named = sum(bool(row["scheme_name"]) for row in schemes) / total
    if named < 1.0:
        raise UpstreamFormatError(
            "AMFI NAVAll.txt", f"{(1 - named):.1%} of {total} rows parsed with an empty scheme name"
        )


def parse_amfi_master(payload: str) -> list[dict]:
    """Parse NAVAll.txt content → list of scheme dicts (pure, no I/O).

    Raises `UpstreamFormatError` when the result no longer looks like NAVAll.txt.
    """
    schemes = []
    current_category = None
    current_sub_category = None
    current_fund_house = None
    header_layout = None
    header_seen = False

    for raw_line in payload.strip().split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("scheme code"):
            header_seen = True
            header_layout = _parse_header(line)
            continue

        parts = line.split(";")
        if len(parts) < _MIN_DATA_FIELDS:
            asset_class, sub_category, fund_house = _classify_amfi_header(line)
            if asset_class is not None:
                current_category = asset_class
                current_sub_category = sub_category
            else:
                current_fund_house = fund_house
            continue

        layout = _layout_for(parts, header_layout)
        if layout is None:
            continue
        try:
            scheme_code = int((_cell(parts, layout, "scheme_code") or "").strip())
        except ValueError:
            continue

        isin_reinvestment = _cell(parts, layout, "isin_reinvestment")
        if isin_reinvestment == "-":
            isin_reinvestment = None
        isin_growth = _cell(parts, layout, "isin_growth")
        if isin_growth == "-":
            isin_growth = None

        plan = _cell(parts, layout, "plan")
        option = _cell(parts, layout, "option")
        base_name = _cell(parts, layout, "scheme_name") or ""

        nav = None
        nav_str = _cell(parts, layout, "nav")
        if nav_str and nav_str not in ("N.A.", "-"):
            with contextlib.suppress(ValueError):
                nav = float(nav_str)

        nav_date = None
        date_str = _cell(parts, layout, "nav_date")
        if date_str:
            with contextlib.suppress(ValueError):
                nav_date = dt.strptime(date_str, "%d-%b-%Y").date()

        schemes.append(
            {
                "scheme_code": scheme_code,
                "isin_growth": isin_growth,
                "isin_reinvestment": isin_reinvestment,
                "scheme_name": compose_scheme_name(base_name, plan, option),
                "plan": plan,
                "option": option,
                "nav": nav,
                "nav_date": nav_date,
                "fund_house": current_fund_house,
                "category": current_category,
                "sub_category": current_sub_category,
            }
        )

    disambiguate_scheme_names(schemes)
    _validate_amfi(schemes, header_seen=header_seen, header_layout=header_layout)
    logger.info("Parsed %d schemes from AMFI master", len(schemes))
    return schemes


def _option_from_isins(row: dict) -> str | None:
    if row.get("isin_reinvestment"):
        return "IDCW"
    if row.get("isin_growth"):
        return "Growth"
    return None


def disambiguate_scheme_names(schemes: list[dict]) -> None:
    """Give every scheme a unique name in place: infer the option from the ISINs, then append the code on ties."""
    by_name: dict[str, list[dict]] = {}
    for row in schemes:
        by_name.setdefault(row["scheme_name"], []).append(row)
    for name, rows in by_name.items():
        if len(rows) < 2:
            continue
        for row in rows:
            if row.get("plan") is None and row.get("option") is None:
                row["option"] = _option_from_isins(row)
                row["scheme_name"] = compose_scheme_name(name, None, row["option"])
        by_variant: dict[str, list[dict]] = {}
        for row in rows:
            by_variant.setdefault(row["scheme_name"], []).append(row)
        for variant_rows in by_variant.values():
            if len(variant_rows) > 1:
                for row in variant_rows:
                    row["scheme_name"] = f"{row['scheme_name']} ({row['scheme_code']})"


def fetch_amfi_master() -> list[dict]:
    """Download AMFI NAVAll.txt → list of scheme dicts (see `parse_amfi_master`)."""
    logger.info("Fetching AMFI master data from %s", AMFI_NAV_ALL_URL)
    resp = httpx.get(AMFI_NAV_ALL_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return parse_amfi_master(resp.text)
