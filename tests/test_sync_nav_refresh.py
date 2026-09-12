"""The routine NAV refresh must not retry, run after run, a fund no source knows.

Synthetic-code funds whose stale tradebook spelling no source recognises stayed 'pending'
forever and produced the same warning on every refresh. A definitive miss on a fund with no
NAV history is recorded as 'unavailable' — the state the Settings retry picker exists for."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from data.exceptions import SourceNotFound
from services import sync_service as sync


def _frame(name: str) -> pl.DataFrame:
    return pl.DataFrame({"date": [date(2026, 9, 12)], "nav": [10.0], "schemeName": [name], "scheme_code": [-19]})


@pytest.fixture
def marked(monkeypatch):
    marked: list[str] = []
    monkeypatch.setattr(sync, "mark_nav_unavailable", marked.append)
    monkeypatch.setattr(sync, "save_nav_df", lambda df: None)
    return marked


def test_first_fetch_miss_marks_the_fund_unavailable(monkeypatch, marked):
    monkeypatch.setattr(sync, "last_nav_date_by_code", lambda codes: {})

    def miss(name, code):
        raise SourceNotFound("AdvisorKhoj overview", name)

    monkeypatch.setattr(sync, "fetch_single_nav", miss)

    result = sync.update_nav_incremental(["Stale Fund - Growth"], name_to_code={"Stale Fund - Growth": -19})

    assert marked == ["Stale Fund - Growth"]
    assert [n for n, _ in result.failures] == ["Stale Fund - Growth"]


def test_a_miss_on_a_fund_with_history_is_not_a_status_change(monkeypatch, marked):
    monkeypatch.setattr(sync, "last_nav_date_by_code", lambda codes: {-19: date(2026, 9, 10)})

    def miss(name, code):
        raise SourceNotFound("AdvisorKhoj overview", name)

    monkeypatch.setattr(sync, "fetch_single_nav", miss)

    sync.update_nav_incremental(["Live Fund - Growth"], name_to_code={"Live Fund - Growth": -19})

    assert marked == []


def test_a_transient_error_on_a_first_fetch_is_not_a_status_change(monkeypatch, marked):
    monkeypatch.setattr(sync, "last_nav_date_by_code", lambda codes: {})

    def timeout(name, code):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(sync, "fetch_single_nav", timeout)

    result = sync.update_nav_incremental(["New Fund - Growth"], name_to_code={"New Fund - Growth": -19})

    assert marked == []
    assert len(result.failures) == 1


def test_a_first_fetch_that_succeeds_is_saved(monkeypatch, marked):
    monkeypatch.setattr(sync, "last_nav_date_by_code", lambda codes: {})
    monkeypatch.setattr(sync, "fetch_single_nav", lambda name, code: _frame(name))
    saved: list[pl.DataFrame] = []
    monkeypatch.setattr(sync, "save_nav_df", saved.append)

    result = sync.update_nav_incremental(["New Fund - Growth"], name_to_code={"New Fund - Growth": -19})

    assert marked == []
    assert result.updated_count == 1 and len(saved) == 1
