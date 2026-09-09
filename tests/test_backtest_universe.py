"""Universe resolution and input loading with the repository seam monkeypatched — no DB."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import polars as pl
import pytest

from services.backtest import universe as universe_module
from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.universe import BasketInputs, load_backtest_inputs, resolve_universe
from stocks.constants import NIFTY_50
from strategies.basket import BasketStrategy

CALENDAR_START = date(2023, 1, 2)


def _calendar(days: int = 400) -> list[date]:
    return [d.date() for d in pd.bdate_range(CALENDAR_START, periods=days)]


def _ohlcv(symbol: str, days: list[date], base: float = 100.0) -> pl.DataFrame:
    n = len(days)
    close = base + np.arange(n, dtype=float)
    return pl.DataFrame(
        {
            "Symbol": [symbol] * n,
            "Date": days,
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": [1000] * n,
        },
        schema={
            "Symbol": pl.Utf8,
            "Date": pl.Date,
            "Open": pl.Float64,
            "High": pl.Float64,
            "Low": pl.Float64,
            "Close": pl.Float64,
            "Volume": pl.Int64,
        },
    )


class _Strategy(BasketStrategy):
    name = "Universe Test"
    startup_candle_count = 20
    informative_symbols = ("^NSEI",)


def _config(spec: UniverseSpec, **overrides) -> BacktestConfig:
    base = dict(
        strategy=_Strategy.name,
        params={},
        universe=spec,
        start=date(2023, 6, 1),
        end=date(2023, 12, 29),
        adjust_splits=False,
    )
    base.update(overrides)
    return BacktestConfig(**base)


@pytest.fixture
def seam(monkeypatch):
    calendar = _calendar()
    ensured: list[tuple[str, date, date]] = []
    state = {
        "calendar": calendar,
        "ensured": ensured,
        "histories": {"A": calendar, "B": calendar, "SHORT": calendar[-10:]},
        "kinds": {"A": "equity", "B": "etf", "SHORT": "equity"},
    }

    def fake_trading_days(start, end):
        return [d for d in state["calendar"] if start <= d <= end]

    def fake_ensure(symbol, start, end):
        ensured.append((symbol, start, end))
        days = [d for d in state["histories"].get(symbol, state["calendar"]) if start <= d <= end]
        return _ohlcv(symbol, days).drop("Symbol")

    def fake_load_many(symbols, start, end):
        parts = []
        for symbol in sorted(symbols):
            days = [d for d in state["histories"].get(symbol, []) if start <= d <= end]
            if days:
                parts.append(_ohlcv(symbol, days))
        return pl.concat(parts) if parts else _ohlcv("A", [])

    monkeypatch.setattr(universe_module, "nse_trading_days", fake_trading_days)
    monkeypatch.setattr(universe_module, "ensure_stock_data", fake_ensure)
    monkeypatch.setattr(universe_module, "load_stock_ohlcv_many", fake_load_many)
    monkeypatch.setattr(
        universe_module, "load_instrument_kinds", lambda symbols: {s: state["kinds"][s] for s in symbols}
    )
    monkeypatch.setattr(
        universe_module, "index_constituents", lambda name: {"NIFTY BANK": ["HDFCBANK", "SBIN"]}.get(name, [])
    )
    return state


# ---------------------------------------------------------------- resolve_universe


def test_resolve_universe_from_index_list_and_watchlist(seam):
    assert resolve_universe(UniverseSpec("index", name="NIFTY BANK")) == ["HDFCBANK", "SBIN"]
    assert resolve_universe(UniverseSpec("index", name="NIFTY 50")) == list(NIFTY_50)
    assert resolve_universe(UniverseSpec("index", name="NIFTY UNKNOWN")) == []
    listed = resolve_universe(UniverseSpec("list", symbols=("RELIANCE.NS", "TCS", "RELIANCE", " infy ")))
    assert listed == ["RELIANCE", "TCS", "INFY"]
    assert resolve_universe(UniverseSpec("watchlist"), watchlist=["A", "B", "A"]) == ["A", "B"]
    assert resolve_universe(UniverseSpec("watchlist"), watchlist=None) == []


# ---------------------------------------------------------------- load_backtest_inputs


def test_inputs_have_warm_up_calendar_reindexed_frames_and_metadata(seam):
    config = _config(UniverseSpec("list", symbols=("A", "B", "SHORT", "MISSING")))
    progress: list[dict] = []
    inputs = load_backtest_inputs(config, _Strategy, progress_cb=lambda **f: progress.append(f))

    assert isinstance(inputs, BasketInputs)
    assert inputs.calendar[inputs.start_pos].date() == date(2023, 6, 1)
    assert inputs.start_pos == 20
    assert inputs.calendar[-1].date() == date(2023, 12, 29)
    assert list(inputs.frames) == ["A", "B"]
    assert inputs.skipped == ["SHORT", "MISSING"]
    assert inputs.instrument_kinds == {"A": "equity", "B": "etf"}
    for frame in inputs.frames.values():
        assert frame.index.equals(inputs.calendar)
        assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert frame["Close"].notna().all()
    assert inputs.benchmark is not None and inputs.benchmark.index.equals(inputs.calendar)
    assert set(inputs.informative) == {"^NSEI"}
    assert inputs.informative["^NSEI"].index.equals(inputs.calendar)
    assert {c["phase"] for c in progress if "phase" in c} >= {"Loading data"}
    ensured_symbols = [s for s, _, _ in seam["ensured"]]
    assert ensured_symbols[:4] == ["A", "B", "SHORT", "MISSING"]
    assert "^NSEI" in ensured_symbols
    first_start = seam["ensured"][0][1]
    assert first_start == inputs.calendar[0].date()
    assert not any("survivorship" in w.lower() for w in inputs.warnings)


def test_gaps_in_a_symbol_history_become_nan_rows(seam):
    calendar = seam["calendar"]
    seam["histories"]["A"] = [d for d in calendar if d != date(2023, 7, 3)]
    inputs = load_backtest_inputs(_config(UniverseSpec("list", symbols=("A",))), _Strategy)
    assert np.isnan(inputs.frames["A"].loc[pd.Timestamp("2023-07-03"), "Close"])
    assert inputs.frames["A"]["Close"].notna().sum() == len(inputs.calendar) - 1


def test_index_universe_records_a_survivorship_warning(seam):
    seam["histories"]["HDFCBANK"] = seam["calendar"]
    seam["histories"]["SBIN"] = seam["calendar"]
    seam["kinds"].update({"HDFCBANK": "equity", "SBIN": "equity"})
    inputs = load_backtest_inputs(_config(UniverseSpec("index", name="NIFTY BANK")), _Strategy)
    assert list(inputs.frames) == ["HDFCBANK", "SBIN"]
    assert any("survivorship" in w.lower() for w in inputs.warnings)


def test_insufficient_warm_up_moves_the_start_and_warns(seam):
    seam["calendar"] = _calendar()[95:]
    seam["histories"] = {"A": seam["calendar"]}
    seam["kinds"] = {"A": "equity"}
    config = _config(UniverseSpec("list", symbols=("A",)), start=seam["calendar"][5])
    inputs = load_backtest_inputs(config, _Strategy)
    assert inputs.start_pos == 20
    effective = inputs.calendar[20].date()
    assert effective > config.start
    assert any(effective.isoformat() in w for w in inputs.warnings)


def test_no_trading_days_in_range_raises(seam):
    config = _config(UniverseSpec("list", symbols=("A",)), start=date(2030, 1, 1), end=date(2030, 2, 1))
    with pytest.raises(ValueError):
        load_backtest_inputs(config, _Strategy)


def test_split_adjustment_is_applied_when_configured(seam):
    calendar = seam["calendar"]
    split_day = date(2023, 8, 1)
    seam["histories"] = {"A": calendar}
    seam["kinds"] = {"A": "equity"}

    def split_frame(symbols, start, end):
        days = [d for d in calendar if start <= d <= end]
        close = np.array([1000.0 if d < split_day else 100.0 for d in days])
        return pl.DataFrame(
            {
                "Symbol": ["A"] * len(days),
                "Date": days,
                "Open": close,
                "High": close,
                "Low": close,
                "Close": close,
                "Volume": [1000] * len(days),
            },
            schema={
                "Symbol": pl.Utf8,
                "Date": pl.Date,
                "Open": pl.Float64,
                "High": pl.Float64,
                "Low": pl.Float64,
                "Close": pl.Float64,
                "Volume": pl.Int64,
            },
        )

    universe_module.load_stock_ohlcv_many = split_frame
    adjusted = load_backtest_inputs(_config(UniverseSpec("list", symbols=("A",)), adjust_splits=True), _Strategy)
    raw = load_backtest_inputs(_config(UniverseSpec("list", symbols=("A",)), adjust_splits=False), _Strategy)
    assert adjusted.frames["A"]["Close"].iloc[0] == pytest.approx(100.0)
    assert adjusted.frames["A"]["Volume"].iloc[0] == pytest.approx(10_000.0)
    assert raw.frames["A"]["Close"].iloc[0] == pytest.approx(1000.0)


def test_symbol_fetch_failure_is_skipped_with_a_warning(seam):
    original = universe_module.ensure_stock_data

    def flaky(symbol, start, end):
        if symbol == "B":
            raise RuntimeError("boom")
        return original(symbol, start, end)

    universe_module.ensure_stock_data = flaky
    inputs = load_backtest_inputs(_config(UniverseSpec("list", symbols=("A", "B"))), _Strategy)
    assert list(inputs.frames) == ["A"]
    assert inputs.skipped == ["B"]
    assert any("B" in w for w in inputs.warnings)


def test_startup_uses_the_parametrised_strategy_instance(seam):
    from strategies.parameters import IntParameter

    class Parametrised(BasketStrategy):
        name = "Parametrised"
        lookback = IntParameter(10, 5, 60)

        @property
        def startup_candle_count(self) -> int:
            return self.lookback + 1

    config = _config(UniverseSpec("list", symbols=("A",)), params={"lookback": 30})
    inputs = load_backtest_inputs(config, Parametrised)
    assert inputs.start_pos == 31
    assert inputs.calendar[0].date() == seam["calendar"][seam["calendar"].index(date(2023, 6, 1)) - 31]
    assert inputs.calendar[0].date() + timedelta(days=1) <= date(2023, 6, 1)
