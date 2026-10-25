"""MFAPI (api.mfapi.in): historical NAV and scheme search."""

from __future__ import annotations

import logging
import re
import threading
import time

import httpx
import polars as pl

from data.constants import MFAPI_BASE_URL
from data.exceptions import SourceThrottled, UpstreamFormatError
from data.fetchers._payload import shape_of

logger = logging.getLogger(__name__)

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
        raise UpstreamFormatError("MFAPI", f"expected an object with a 'data' key, got {shape_of(raw)}")
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
        raise UpstreamFormatError("MFAPI search", f"expected a list of schemes, got {shape_of(results)}")
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
