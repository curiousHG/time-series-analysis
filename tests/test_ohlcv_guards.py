"""Guards on fetched OHLCV bars: verified tickers, no phantom weekend bars, no implausible index bars."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import polars as pl
import pytest

from data.exceptions import UpstreamFormatError
from data.fetchers import stock as fetcher
from data.repositories import _ohlcv_io as io
from data.repositories import index_ohlcv as idx


def _mi(ticker: str, closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2026-08-24", periods=len(closes))
    cols = pd.MultiIndex.from_product([[ticker], ["Open", "High", "Low", "Close", "Volume"]])
    data = [[c, c, c, c, 100] for c in closes]
    return pd.DataFrame(data, index=dates, columns=cols)


def test_single_fetch_rejects_a_frame_for_another_ticker(monkeypatch):
    monkeypatch.setattr(fetcher.yf, "download", lambda *a, **k: _mi("CHAMBLFERT.NS", [472.4, 470.0]))
    with pytest.raises(UpstreamFormatError, match="asked for '\\^NDX'"):
        fetcher.fetch_symbol_data("^NDX", start="2026-08-24", end="2026-08-26")


def test_single_fetch_flattens_the_right_ticker(monkeypatch):
    monkeypatch.setattr(fetcher.yf, "download", lambda *a, **k: _mi("^NDX", [29000.0, 29100.0]))
    out = fetcher.fetch_symbol_data("^NDX", start="2026-08-24", end="2026-08-26")
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert out["Close"].iloc[0] == 29000.0


def test_batch_refuses_to_attribute_a_flat_frame_to_the_first_of_many(monkeypatch):
    flat = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
        index=pd.bdate_range("2026-08-24", periods=1),
    )
    monkeypatch.setattr(fetcher.yf, "download", lambda *a, **k: flat)
    with pytest.raises(UpstreamFormatError, match="attribution lost"):
        fetcher.fetch_symbols_batch(["A.NS", "B.NS"], dt.date(2026, 8, 24), dt.date(2026, 8, 26))


def test_batch_rejects_unrequested_tickers(monkeypatch):
    monkeypatch.setattr(fetcher.yf, "download", lambda *a, **k: _mi("Z.NS", [1.0]))
    with pytest.raises(UpstreamFormatError, match="not requested"):
        fetcher.fetch_symbols_batch(["A.NS", "B.NS"], dt.date(2026, 8, 24), dt.date(2026, 8, 26))


def test_phantom_weekend_bars_are_dropped_but_special_sessions_kept(monkeypatch):
    budget_day = dt.date(2026, 2, 1)  # Sunday, real NSE session
    monkeypatch.setattr(io, "_special_session_dates", lambda: {budget_day})
    df = pl.DataFrame(
        {
            "Date": [dt.date(2026, 1, 30), dt.date(2026, 1, 31), budget_day, dt.date(2026, 2, 2)],
            "Close": [1.0, 2.0, 3.0, 4.0],
        }
    ).with_columns(pl.col("Date").cast(pl.Date))
    kept = io._drop_phantom_weekend_bars(df, "TATAMOTORS")
    assert kept["Date"].to_list() == [dt.date(2026, 1, 30), budget_day, dt.date(2026, 2, 2)]


def test_implausible_index_bars_are_rejected_against_prior_close(monkeypatch):
    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def exec(self, stmt):
            class _R:
                def first(self_inner):
                    return 29000.0

            return _R()

    monkeypatch.setattr(idx, "get_session", lambda: _S())
    frame = pl.DataFrame(
        {"Date": [dt.date(2026, 7, 1), dt.date(2026, 7, 2), dt.date(2026, 7, 3)], "Close": [472.4, 29100.0, 29050.0]}
    ).with_columns(pl.col("Date").cast(pl.Date))
    kept = idx._reject_implausible_index_bars("^NDX", frame)
    assert kept["Close"].to_list() == [29100.0, 29050.0]
