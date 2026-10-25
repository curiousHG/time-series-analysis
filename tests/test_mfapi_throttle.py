"""MFAPI throttling must slow a bulk NAV refresh down, never turn into per-fund failures.

At 16 workers MFAPI answered 1,668 of a 3,508-fund refresh with HTTP 429. fetch_single_nav fell
back to AdvisorKhoj, which doesn't know most funds by their AMFI name, so every throttled fund
surfaced as "AdvisorKhoj overview has no record" — and a fund with no NAV history would have
been marked unavailable for good."""

from __future__ import annotations

from datetime import date

import httpx
import polars as pl
import pytest

from data.exceptions import SourceNotFound, SourceThrottled
from data.fetchers import mutual_fund as mf
from data.repositories import nav
from services import sync_service as sync

_URL = "https://api.mfapi.in/mf/119591"
_BODY = {"data": [{"date": "24-10-2026", "nav": "10.5"}]}


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(mf.time, "sleep", slept.append)
    monkeypatch.setattr(mf, "_MFAPI_COOLDOWN", mf._Cooldown())
    return slept


def _responses(monkeypatch, statuses: list[int]) -> list[int]:
    calls: list[int] = []

    def fake_get(url, timeout):
        status = statuses[min(len(calls), len(statuses) - 1)]
        calls.append(status)
        return httpx.Response(status, json=_BODY, request=httpx.Request("GET", url))

    monkeypatch.setattr(mf.httpx, "get", fake_get)
    return calls


def test_a_429_is_waited_out_and_retried(monkeypatch, no_sleep):
    calls = _responses(monkeypatch, [429, 429, 200])

    df = mf.fetch_nav_from_mfapi("119591", "Some Fund - Direct Plan - Growth")

    assert calls == [429, 429, 200]
    assert df.height == 1
    assert no_sleep and all(s > 0 for s in no_sleep)


def test_persistent_429s_raise_source_throttled(monkeypatch, no_sleep):
    calls = _responses(monkeypatch, [429])

    with pytest.raises(SourceThrottled):
        mf.fetch_nav_from_mfapi("119591", "Some Fund - Direct Plan - Growth")

    assert len(calls) == len(mf._MFAPI_BACKOFF_SECONDS) + 1


def test_one_threads_429_pauses_every_thread():
    cooldown = mf._Cooldown()
    cooldown.hold(30)
    cooldown.hold(5)
    assert cooldown._resume_at - mf.time.monotonic() > 20


def test_fetch_single_nav_does_not_fall_back_to_advisorkhoj_on_a_throttle(monkeypatch):
    def throttled(code, name):
        raise SourceThrottled("MFAPI", 6)

    def advisorkhoj(name):
        raise AssertionError("AdvisorKhoj must not be asked when MFAPI is throttling")

    monkeypatch.setattr(nav, "fetch_nav_from_mfapi", throttled)
    monkeypatch.setattr(nav, "fetch_nav_from_advisorkhoj", advisorkhoj)

    with pytest.raises(SourceThrottled):
        nav.fetch_single_nav("Some Fund - Direct Plan - Growth", scheme_code=119591)


def test_a_throttled_first_fetch_does_not_mark_the_fund_unavailable(monkeypatch):
    marked: list[str] = []
    monkeypatch.setattr(sync, "mark_nav_unavailable", marked.append)
    monkeypatch.setattr(sync, "last_nav_date_by_code", lambda codes: {})

    def throttled(name, code):
        raise SourceThrottled("MFAPI", 6)

    monkeypatch.setattr(sync, "fetch_single_nav", throttled)

    result = sync.update_nav_incremental(["New Fund - Growth"], name_to_code={"New Fund - Growth": 1})

    assert marked == []
    assert result.failures == [("New Fund - Growth", "MFAPI rate-limited the request (HTTP 429) 6 times in a row")]


def test_failure_reasons_drop_the_fund_name_so_they_group():
    a, b = "Alpha Fund - Growth", "Beta Fund - Growth"
    request = httpx.Request("GET", "https://www.advisorkhoj.com/mutual-funds-research/Alpha%20Fund")

    assert sync.failure_reason(SourceNotFound("AdvisorKhoj portfolio", a), a) == sync.failure_reason(
        SourceNotFound("AdvisorKhoj portfolio", b), b
    )
    assert sync.failure_reason(SourceNotFound("AdvisorKhoj portfolio", a), a) == "AdvisorKhoj portfolio has no record"
    http = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
    assert sync.failure_reason(http, a) == "HTTP 404 from www.advisorkhoj.com"
    assert sync.failure_reason(ValueError(f"Empty portfolio response for {a!r}"), a) == (
        "ValueError: Empty portfolio response for the fund"
    )


def test_refresh_returns_the_failures_for_the_result_panel(monkeypatch):
    monkeypatch.setattr(sync, "mark_nav_unavailable", lambda name: None)
    monkeypatch.setattr(sync, "save_nav_df", lambda df: None)
    monkeypatch.setattr(sync, "last_nav_date_by_code", lambda codes: {1: date(2026, 10, 1), 2: date(2026, 10, 1)})

    def fetch(name, code):
        if code == 2:
            raise SourceThrottled("MFAPI", 6)
        return pl.DataFrame({"date": [date(2026, 10, 24)], "nav": [10.0], "schemeName": [name], "scheme_code": [1]})

    monkeypatch.setattr(sync, "fetch_single_nav", fetch)

    out = sync.refresh_all_fund_data(["Ok Fund", "Slow Fund"], name_to_code={"Ok Fund": 1, "Slow Fund": 2}, scope="nav")

    assert (out["nav_updated"], out["nav_skipped"], out["nav_failed"]) == (1, 0, 1)
    assert [name for name, _ in out["nav_failures"]] == ["Slow Fund"]
