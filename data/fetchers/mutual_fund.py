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
from data.exceptions import SourceNotFound, UpstreamFormatError

logger = logging.getLogger("data.fetchers.mutual_fund")


def _shape_of(payload: object) -> str:
    """Short description of an unexpected payload, for `UpstreamFormatError` messages."""
    if isinstance(payload, dict):
        return f"object with keys {sorted(payload)[:8]}"
    if isinstance(payload, list):
        return f"list of {len(payload)}"
    return type(payload).__name__


def fetch_nav_from_mfapi(scheme_code: str, scheme_name: str) -> pl.DataFrame:
    """Fetch historical NAV from MFAPI → (date, nav, schemeName) DataFrame."""
    url = f"{MFAPI_BASE_URL}/{scheme_code}"
    logger.debug("Fetching NAV from MFAPI: code=%s name=%s", scheme_code, scheme_name)

    resp = httpx.get(url, timeout=30)
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
    logger.debug("Fetching portfolio from AdvisorKhoj: slug=%s", slug)
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
    try:
        data = resp.json()
    except ValueError as e:
        # An unknown slug is answered with HTTP 200 and the HTML homepage.
        raise SourceNotFound("AdvisorKhoj portfolio", slug) from e
    if not data:
        raise ValueError(f"Empty portfolio response from AdvisorKhoj for slug '{slug}'")
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


def fetch_fund_metadata(scheme_name: str, *, lookup_key: str | None = None) -> dict:
    """Scrape AdvisorKhoj fund overview page → metadata dict (missing fields = None).

    `lookup_key` is the URL key when it differs from the record's name — the verified
    AdvisorKhoj slug from the holdings resolver, which the overview page accepts where a
    current AMFI name gets the homepage. The returned dict is still keyed on `scheme_name`.
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

    # SEBI riskometer: the level is encoded in the image filename
    # (e.g. /resources/images/common/Riskometer-Very-High.png → "Very High").
    for img in soup.find_all("img", alt="riskometer"):
        m = re.search(r"Riskometer-([\w-]+)\.png", img.get("src") or "")
        if m:
            out["risk_level"] = m.group(1).replace("-", " ")
            break

    # Investment objective: the paragraph under its section heading.
    for h in soup.select("h1, h2, h3, h4"):
        if "investment objective" in h.get_text(strip=True).lower():
            para = h.find_next("p")
            if para:
                text = re.sub(r"\s+", " ", para.get_text(" ", strip=True)).strip()
                out["investment_objective"] = text[:2000] or None
            break

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
    """Fetch raw overview HTML. scheme_name e.g. 'SBI ELSS Tax Saver FUND - REGULAR PLAN-GROWTH'.

    Whitespace is collapsed first: AMFI names sometimes carry double spaces, and
    AdvisorKhoj's redirect to its canonical slug URL corrupts on them (the Location header
    splices the base URL into the middle of the slug → guaranteed 404).
    """
    scheme_name = re.sub(r"\s+", " ", scheme_name).strip()
    url = BASE_OVERVIEW_URL.format(scheme_name=quote(scheme_name))

    resp = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    if "sch_over_table" not in resp.text:
        # Unknown scheme names get HTTP 200 + the site homepage, not a 404. Parsing that
        # yields a row of Nones which the repo used to store and mark "available".
        raise SourceNotFound("AdvisorKhoj overview", scheme_name)
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

# Canonical asset classes. AMFI spells each class several ways across headers — singular and
# plural ("Equity Scheme" / "Equity Schemes"), footnote-marked ("Solution Oriented Schemes **"),
# and legacy close-ended wording ("Income", "Growth") — which previously landed in the category
# dim as distinct values. Keys here are post-`_normalise_class` (lowercased) forms.
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

# SEBI files index funds, ETFs and FoFs under the catch-all class "Other Scheme", naming the real
# class only in the sub-category — while the very same funds also arrive under dedicated
# "Index Funds" / "Exchange Traded Funds (ETFs)" / "Fund of Funds Scheme (Domestic)" headers.
# Refining "Other" by its sub-category keeps both routes landing on one category.
_SUB_CLASS_ALIASES = {
    "index funds": "Index",
    "gold etf": "ETF",
    "other etfs": "ETF",
    "fof domestic": "Fund of Funds",
    "fof overseas": "Fund of Funds",
}


def _normalise_class(text: str) -> str:
    """Collapse an AMFI class label to its bare form: no trailing footnote asterisks, no
    "Scheme"/"Schemes" filler, straight apostrophes, single spaces."""
    s = text.replace("’", "'")  # noqa: RUF001 — AMFI emits a curly apostrophe in some class names
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\*+$", "", s)
    s = re.sub(r"\s+Schemes?\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canonical_asset_class(text: str) -> str:
    """Map an AMFI class label onto `_ASSET_CLASSES`. Unknown labels fall back to "Other"
    and are logged, so a new SEBI class shows up in the logs instead of silently splitting
    the category dim."""
    bare = _normalise_class(text)
    if not bare:
        return "Other"
    canonical = _ASSET_CLASS_ALIASES.get(bare.lower())
    if canonical is None:
        logger.warning("Unmapped AMFI asset class %r — filed as 'Other'", bare)
        return "Other"
    return canonical


def _classify_amfi_header(line: str) -> tuple[str | None, str | None, str | None]:
    """Classify a non-data AMFI line as (asset_class, sub_category, fund_house).

    Category headers are SEBI scheme-type lines (`Open/Close Ended Schemes(<Class> Scheme - <Sub>)`)
    → a canonical asset_class ("Equity"/"Debt"/"Hybrid"/…) + the granular sub_category
    ("Large Cap Fund"). Headers without the " - " split use the whole inner text as both.
    Everything else is an AMC/fund-house line — these can also contain parentheses (e.g.
    "IL&FS Mutual Fund (IDF)"), which the old paren-only heuristic mis-filed as a category.
    """
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


# ---- NAVAll.txt column layout ----------------------------------------------------------
# AMFI moved from 6 columns to 8 in 2025, splitting the plan/option suffix out of the scheme
# name into their own `Plan` and `Option` columns:
#   old: Code;ISIN Growth;ISIN Reinvestment;Scheme Name;NAV;Date
#   new: Code;ISIN Growth;ISIN Reinvestment;Scheme Name;Plan;Option;NAV;Date
# Layout is read from the file's own header row, so a further column move is absorbed without
# a code change; the positional maps are the fallback when the header is missing or unreadable.

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
    """Pick the column map for one data row: the file's header when every index it names is
    present, else the widest positional layout the row can satisfy."""
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
    """Full scheme name, "<base> - <plan> - <option>".

    AMFI's `Scheme Name` column is now the *base* name shared by every plan/option variant of a
    fund, so on its own it is neither unique (one name covered up to 38 scheme codes) nor
    parseable by `detect_plan`/`detect_option`. Re-attaching AMFI's own plan/option text restores
    both, and keeps the " - Direct - Growth" shape that AdvisorKhoj slugs and the display helpers
    already expect. Rows with no plan/option (closed-ended FMPs) keep the bare name.
    """
    return " - ".join(p for p in (base, plan, option) if p)


# Output-shape floors for `_validate_amfi`. AMFI has published >10K schemes for years and every
# live row carries a NAV, so these sit far below normal while still catching a column shift.
_AMFI_MIN_SCHEMES = 1_000
_AMFI_MIN_VALUE_COVERAGE = 0.5


def _validate_amfi(schemes: list[dict], *, header_seen: bool, header_layout: dict[str, int] | None) -> None:
    """Fail loudly when the parsed result doesn't look like NAVAll.txt any more.

    The checks are on the parsed rows, not just the header: the last AMFI change produced a
    full-sized result in which every NAV was None.
    """
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
    """Parse NAVAll.txt content → list of scheme dicts. Pure: no I/O, so it's directly testable
    against captured fixtures.

    Lines with fewer than `_MIN_DATA_FIELDS` semicolon-separated fields are scheme-type
    (category) or fund-house headers. Raises `UpstreamFormatError` when the result no longer
    looks like NAVAll.txt.
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
            # Scheme-type (asset class + sub-category) or fund-house header.
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
    """Give every scheme a unique name, in place.

    AMFI leaves Plan/Option blank on ~5,700 open-ended rows, so all variants of such a fund
    arrive with one identical name (Motilal Oswal Midcap Fund: 4 codes). Every name-keyed path
    (NAV refresh, portfolio value, screener) then mixes the variants. The option is inferred
    from the ISIN columns (a reinvestment ISIN means IDCW) and stored in `option`; when that
    still leaves a tie the scheme code is appended, which `base_name` strips again.
    """
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
