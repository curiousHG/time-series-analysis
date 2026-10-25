"""AMFI NAVAll.txt: master scheme list with ISINs and categories."""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import datetime as dt

import httpx

from data.constants import AMFI_NAV_ALL_URL
from data.exceptions import UpstreamFormatError

logger = logging.getLogger(__name__)


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
