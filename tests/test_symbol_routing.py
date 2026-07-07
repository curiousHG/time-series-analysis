"""Symbol → table/source routing rules (stocks.constants) + the OHLCV despike guard.

These pin the datatype routing that the benchmark fix depends on: bhavcopy-name indices
("Nifty Midcap 150") must route to index_ohlcv, equities to stock_ohlcv, and one-day yahoo
glitches must be dropped WITHOUT touching real splits (level shifts)."""

from __future__ import annotations

import datetime as dt

from data.repositories.stock_ohlcv import _despike
from stocks.constants import is_index_symbol


def test_caret_fx_and_proxy_symbols_are_indices():
    assert is_index_symbol("^NSEI")
    assert is_index_symbol("^GSPC")
    assert is_index_symbol("INR=X")
    assert is_index_symbol("URTH")
    assert is_index_symbol("ACWI")


def test_bhavcopy_names_any_case_are_indices():
    # NSE spells its own index names inconsistently — all must route to index_ohlcv.
    assert is_index_symbol("Nifty Midcap 150")
    assert is_index_symbol("NIFTY Midcap 100")
    assert is_index_symbol("NIFTY SME EMERGE")
    assert is_index_symbol("India VIX")


def test_equities_are_not_indices():
    for sym in ("RELIANCE", "M&M", "BAJAJ-AUTO", "LTIM", "AAPL", "GOLDBEES"):
        assert not is_index_symbol(sym), sym


def _rows(closes: list[float]) -> list[dict]:
    d0 = dt.date(2003, 4, 10)
    return [
        {"date": d0 + dt.timedelta(days=i), "symbol": "T", "close": c, "open": c, "high": c, "low": c, "volume": 1}
        for i, c in enumerate(closes)
    ]


def test_despike_drops_one_day_glitch():
    # CIPLA 2003-04-14 shape: 49.3 → 3.9 → 49.5 (drop then full recovery next session).
    out = _despike(_rows([49.0, 49.3, 3.9, 49.5, 50.0]))
    assert [r["close"] for r in out] == [49.0, 49.3, 49.5, 50.0]


def test_despike_keeps_real_splits():
    # A 1:10 split is a level SHIFT — the new level persists, so nothing is dropped.
    closes = [900.0, 910.0, 91.0, 92.0, 93.0]
    out = _despike(_rows(closes))
    assert [r["close"] for r in out] == closes


def test_despike_keeps_normal_series_and_short_series():
    closes = [100.0, 101.0, 99.5, 102.0]
    assert [r["close"] for r in _despike(_rows(closes))] == closes
    assert _despike(_rows([100.0])) == _rows([100.0])
    assert _despike([]) == []
