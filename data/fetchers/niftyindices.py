"""niftyindices.com total-return index history (TRI and net TRI) for any NSE index, by name."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

import httpx
import polars as pl

from data.constants import (
    NIFTYINDICES_BASE_URL,
    NIFTYINDICES_EARLIEST,
    NIFTYINDICES_HEADERS,
    NIFTYINDICES_HISTORY_PAGE,
    NIFTYINDICES_TRI_PATH,
)
from data.exceptions import UpstreamFormatError
from data.fetchers.schemas import NiftyTriRows, validate_upstream

logger = logging.getLogger(__name__)

_SOURCE = "niftyindices TRI"
_BOOTSTRAP_TIMEOUT_S = 10
_REQUEST_TIMEOUT_S = 90
TRI_SCHEMA = {"date": pl.Date, "tri": pl.Float64, "ntr": pl.Float64}


def _num(v: object) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def tri_frame(rows: NiftyTriRows) -> pl.DataFrame:
    """Parse validated TRI rows into TRI_SCHEMA, oldest first; rows without a positive TRI are dropped."""
    parsed = [
        {
            "date": datetime.strptime(r["Date"].strip(), "%d %b %Y").date(),
            "tri": _num(r["TotalReturnsIndex"]),
            "ntr": _num(r.get("NTR_Value")),
        }
        for r in rows
    ]
    frame = pl.DataFrame(parsed, schema=TRI_SCHEMA)
    return frame.filter(pl.col("tri").is_not_null()).unique(subset=["date"], keep="last").sort("date")


def _decode(resp: httpx.Response) -> object:
    try:
        body = resp.json()
    except json.JSONDecodeError as e:
        raise UpstreamFormatError(_SOURCE, f"non-JSON answer (HTTP {resp.status_code})") from e
    if isinstance(body, dict) and "d" in body:
        return json.loads(body["d"]) if isinstance(body["d"], str) else body["d"]
    return body


def fetch_index_tri(name: str, start: date | None = None, end: date | None = None) -> pl.DataFrame:
    """Daily TRI/NTR for the NSE index `name` (case-insensitive) between `start` and `end`, oldest first."""
    start_s = start.strftime("%d-%b-%Y") if start else NIFTYINDICES_EARLIEST
    end_s = (end or date.today()).strftime("%d-%b-%Y")
    upper = name.upper()
    cinfo = f"{{'name':'{upper}','startDate':'{start_s}','endDate':'{end_s}','indexName':'{upper}'}}"
    with httpx.Client(headers=NIFTYINDICES_HEADERS, timeout=_REQUEST_TIMEOUT_S) as client:
        try:
            client.get(NIFTYINDICES_HISTORY_PAGE, timeout=_BOOTSTRAP_TIMEOUT_S)
        except httpx.HTTPError as e:
            logger.debug("niftyindices cookie bootstrap failed: %s", e)
        resp = client.post(NIFTYINDICES_BASE_URL + NIFTYINDICES_TRI_PATH, json={"cinfo": cinfo})
    rows = validate_upstream(_SOURCE, NiftyTriRows, _decode(resp))
    frame = tri_frame(rows)
    logger.debug("niftyindices TRI %s %s..%s: %d rows", name, start_s, end_s, frame.height)
    return frame
