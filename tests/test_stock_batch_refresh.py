"""Flow tests for the batched-yfinance daily refresh (fetch_symbols_batch shape handling).

yf.download returns DIFFERENT shapes for one vs many tickers (flat vs MultiIndex columns) and
pads missing tickers with all-NaN columns — the fetcher must normalise all of that to a clean
{ticker: frame} map, because the repo upserts whatever it gets."""

from __future__ import annotations

import datetime as dt

import pandas as pd

import data.fetchers.stock as fetchers

D = [dt.date(2026, 7, 6), dt.date(2026, 7, 7)]


def _flat(closes):
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [1, 1]},
        index=pd.DatetimeIndex(D, name="Date"),
    )


def test_multi_ticker_multiindex_is_split_per_ticker(monkeypatch):
    a, b = _flat([100.0, 101.0]), _flat([50.0, 51.0])
    multi = pd.concat({"RELIANCE.NS": a, "AAPL": b}, axis=1)  # MultiIndex: (ticker, field)
    monkeypatch.setattr(fetchers.yf, "download", lambda *args, **kw: multi)

    out = fetchers.fetch_symbols_batch(["RELIANCE.NS", "AAPL"], D[0], D[1])

    assert set(out) == {"RELIANCE.NS", "AAPL"}
    assert list(out["RELIANCE.NS"]["Close"]) == [100.0, 101.0]
    assert list(out["AAPL"]["Close"]) == [50.0, 51.0]


def test_single_ticker_flat_columns_are_wrapped(monkeypatch):
    monkeypatch.setattr(fetchers.yf, "download", lambda *args, **kw: _flat([10.0, 11.0]))

    out = fetchers.fetch_symbols_batch(["TCS.NS"], D[0], D[1])

    assert list(out) == ["TCS.NS"]
    assert list(out["TCS.NS"]["Close"]) == [10.0, 11.0]


def test_all_nan_tickers_are_omitted(monkeypatch):
    good = _flat([100.0, 101.0])
    dead = _flat([float("nan"), float("nan")])
    dead["Volume"] = float("nan")  # yfinance pads a no-data ticker with NaN in EVERY column
    multi = pd.concat({"RELIANCE.NS": good, "DELISTED.NS": dead}, axis=1)
    monkeypatch.setattr(fetchers.yf, "download", lambda *args, **kw: multi)

    out = fetchers.fetch_symbols_batch(["RELIANCE.NS", "DELISTED.NS"], D[0], D[1])

    assert set(out) == {"RELIANCE.NS"}  # a ticker yfinance can't serve just falls out


def test_failure_and_empty_input_return_empty(monkeypatch):
    assert fetchers.fetch_symbols_batch([], D[0], D[1]) == {}

    def _boom(*args, **kw):
        raise RuntimeError("throttled")

    monkeypatch.setattr(fetchers.yf, "download", _boom)
    monkeypatch.setattr(fetchers, "push_notice", lambda *a, **k: None)
    assert fetchers.fetch_symbols_batch(["RELIANCE.NS"], D[0], D[1]) == {}
