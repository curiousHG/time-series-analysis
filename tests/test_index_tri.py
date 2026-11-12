"""niftyindices TRI parsing and the DB-first gap logic of ensure_index_tri."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import polars as pl
import pytest

from data.exceptions import UpstreamFormatError
from data.fetchers import niftyindices
from data.fetchers.niftyindices import TRI_SCHEMA, tri_frame
from data.fetchers.schemas import NiftyTriRows, validate_upstream
from data.repositories import index_tri as repo

_ROWS = [
    {"Index Name": "Nifty 50", "Date": "12 Nov 2026", "TotalReturnsIndex": "38,473.73", "NTR_Value": "33391.35"},
    {"Index Name": "Nifty 50", "Date": "11 Nov 2026", "TotalReturnsIndex": "38400.84", "NTR_Value": "-"},
    {"Index Name": "Nifty 50", "Date": "10 Nov 2026", "TotalReturnsIndex": "-", "NTR_Value": "-"},
]


def test_rows_parse_oldest_first_with_dash_as_missing():
    frame = tri_frame(validate_upstream("niftyindices TRI", NiftyTriRows, _ROWS))
    assert frame["date"].to_list() == [date(2026, 11, 11), date(2026, 11, 12)]
    assert frame["tri"].to_list() == [38400.84, 38473.73]
    assert frame["ntr"].to_list() == [None, 33391.35]


def _response(body: object) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(body).encode(), request=httpx.Request("POST", "http://x"))


@pytest.mark.parametrize("body", [_ROWS, {"d": json.dumps(_ROWS)}])
def test_both_bare_and_d_wrapped_answers_decode(body):
    assert niftyindices._decode(_response(body)) == _ROWS


def test_html_answer_is_an_upstream_format_error():
    resp = httpx.Response(200, content=b"<!DOCTYPE html>", request=httpx.Request("POST", "http://x"))
    with pytest.raises(UpstreamFormatError):
        niftyindices._decode(resp)


def _patch_repo(monkeypatch, stored_range):
    calls: list[tuple] = []

    def fake_fetch(name, start, end):
        calls.append((name, start, end))
        return pl.DataFrame({"date": [end], "tri": [1.0], "ntr": [None]}, schema=TRI_SCHEMA)

    monkeypatch.setattr(repo, "get_index_tri_range", lambda symbol: stored_range)
    monkeypatch.setattr(repo, "fetch_index_tri", fake_fetch)
    monkeypatch.setattr(repo, "save_index_tri", lambda symbol, frame: frame.height)
    monkeypatch.setattr(repo, "load_index_tri", lambda symbol, start, end: pl.DataFrame(schema=TRI_SCHEMA))
    return calls


def test_empty_table_pulls_full_history_once(monkeypatch):
    calls = _patch_repo(monkeypatch, None)
    repo.ensure_index_tri("Nifty 50", end=date(2026, 11, 12))
    assert calls == [("Nifty 50", None, date(2026, 11, 12))]


def test_stored_history_fetches_only_the_forward_gap(monkeypatch):
    last = date(2026, 10, 30)
    calls = _patch_repo(monkeypatch, (date(1999, 6, 30), last))
    repo.ensure_index_tri("Nifty 50", end=date(2026, 11, 12))
    assert calls == [("Nifty 50", last + timedelta(days=1), date(2026, 11, 12))]


def test_recent_history_skips_the_network(monkeypatch):
    calls = _patch_repo(monkeypatch, (date(1999, 6, 30), date(2026, 11, 11)))
    repo.ensure_index_tri("Nifty 50", end=date(2026, 11, 12))
    assert calls == []
