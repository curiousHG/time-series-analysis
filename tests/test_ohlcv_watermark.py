"""Unit tests for the OHLCV availability watermark state machine (data.repositories.ohlcv_watermark).

_ensure_ohlcv is dependency-injected (model / fetch_fn / get_wm / set_wm), so the branchy logic —
empty-streak → unavailable, negative-cache re-hammer guard, backward-gap floor lock — is tested here
with in-memory fakes + monkeypatched DB helpers, no database required.
"""

from __future__ import annotations

import datetime as dt

import pytest

import data.repositories.ohlcv_watermark as wm

START = dt.date(2015, 1, 1)
END = dt.date(2026, 1, 2)


class FakeWm:
    """In-memory stand-in for the stock/index registry watermark row."""

    def __init__(self, status: str | None = None, earliest: dt.date | None = None, streak: int = 0) -> None:
        self.state = wm._Wm(status, earliest, streak)

    def get(self, _symbol: str) -> wm._Wm:
        return self.state

    def set(self, _symbol: str, *, status=None, earliest=wm._UNSET, empty_streak=None) -> None:
        if status is not None:
            self.state.status = status
        if earliest is not wm._UNSET:
            self.state.earliest = earliest
        if empty_streak is not None:
            self.state.streak = empty_streak


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Isolate each test: empty the negative cache and silence notices."""
    wm._empty_fetch_cache.clear()
    monkeypatch.setattr(wm, "push_notice", lambda *a, **k: None)
    monkeypatch.setattr(wm, "_load_ohlcv", lambda *a: "DF")  # return value is irrelevant to the state machine
    yield
    wm._empty_fetch_cache.clear()


def _ranges(monkeypatch, values):
    """Feed successive _date_range(...) return values from `values`."""
    it = iter(values)
    monkeypatch.setattr(wm, "_date_range", lambda *a: next(it))


def _run(fake: FakeWm, fetch_fn, *, flag_unavailable=True):
    return wm._ensure_ohlcv(
        "SYM", START, END, model=object, fetch_fn=fetch_fn,
        get_wm=fake.get, set_wm=fake.set, flag_unavailable=flag_unavailable,
    )


def test_unavailable_short_circuits(monkeypatch):
    """An 'unavailable' watermark returns empty without touching the DB or fetching."""
    monkeypatch.setattr(wm, "_date_range", lambda *a: pytest.fail("must not query when unavailable"))
    fetched = []
    out = _run(FakeWm(status="unavailable"), lambda *a: fetched.append(1))
    assert fetched == []
    assert out.is_empty()


def test_success_marks_available_with_floor(monkeypatch):
    """Fetch that yields data → available, earliest set to the DB min, streak reset."""
    _ranges(monkeypatch, [None, (dt.date(2018, 3, 1), dt.date(2026, 1, 1))])
    fake = FakeWm()
    _run(fake, lambda *a: None)
    assert fake.state.status == "available"
    assert fake.state.earliest == dt.date(2018, 3, 1)
    assert fake.state.streak == 0


def test_empty_fetch_increments_streak_then_unavailable(monkeypatch):
    """Two empty fetches (negative cache cleared between them) flip the symbol to unavailable."""
    fake = FakeWm()
    _ranges(monkeypatch, [None, None])  # existing None, post-fetch still None
    _run(fake, lambda *a: None)
    assert fake.state.status == "pending"
    assert fake.state.streak == 1

    wm._empty_fetch_cache.clear()  # simulate the 1h negative-cache TTL expiring
    _ranges(monkeypatch, [None, None])
    _run(fake, lambda *a: None)
    assert fake.state.status == "unavailable"
    assert fake.state.streak == 2


def test_negative_cache_skips_refetch(monkeypatch):
    """After an empty fetch, a second call within the TTL must NOT re-fetch (anti-hammer)."""
    fetches = []
    monkeypatch.setattr(wm, "_date_range", lambda *a: None)  # always empty
    _run(FakeWm(), lambda *a: fetches.append(1))
    assert len(fetches) == 1
    _run(FakeWm(), lambda *a: fetches.append(1))  # negative-cached → no fetch
    assert len(fetches) == 1


def test_backward_gap_locks_floor_and_skips_next_time(monkeypatch):
    """A backward-gap fetch that surfaces nothing earlier locks earliest=db_min; the next call with
    the same gap skips the (doomed) backward fetch."""
    db_min, db_max = dt.date(2020, 1, 1), dt.date(2026, 1, 1)
    fetches = []
    fake = FakeWm()
    # First call: existing (db_min,db_max); after backward fetch db_min unchanged → floor locks.
    _ranges(monkeypatch, [(db_min, db_max), (db_min, db_max)])
    _run(fake, lambda *a: fetches.append(a), flag_unavailable=False)
    assert fake.state.earliest == db_min
    assert len(fetches) == 1  # the backward fetch happened once

    # Second call, same gap, floor already locked → no backward fetch.
    fetches.clear()
    _ranges(monkeypatch, [(db_min, db_max)])
    _run(fake, lambda *a: fetches.append(a), flag_unavailable=False)
    assert fetches == []
