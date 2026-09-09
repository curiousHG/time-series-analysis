"""Basket engine on synthetic OHLCV: fills, the intrabar ladder, sizing, rank rebalancing, edge cases."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import ZERO, ZERODHA_DELIVERY, ZERODHA_INTRADAY, apply_costs
from services.backtest.engine import (
    TRADE_COLUMNS,
    BacktestResult,
    is_rebalance_day,
    roi_threshold,
    run_engine,
    size_order,
)
from services.backtest.universe import BasketInputs
from strategies.basket import ENTER, ENTER_TAG, EXIT, EXIT_TAG, SCORE, BasketStrategy

START = "2024-01-01"


def _frame(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volume: int = 1_000_000,
) -> pd.DataFrame:
    idx = pd.bdate_range(START, periods=len(closes))
    close = pd.Series(closes, index=idx, dtype=float)
    open_ = pd.Series(opens, index=idx, dtype=float) if opens is not None else close.copy()
    high = pd.Series(highs, index=idx, dtype=float) if highs is not None else np.maximum(open_, close)
    low = pd.Series(lows, index=idx, dtype=float) if lows is not None else np.minimum(open_, close)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": float(volume)}, index=idx)


def _inputs(
    frames: dict[str, pd.DataFrame],
    *,
    start_pos: int = 0,
    benchmark: pd.DataFrame | None = None,
    kinds: dict[str, str] | None = None,
) -> BasketInputs:
    calendar = next(iter(frames.values())).index
    return BasketInputs(
        calendar=calendar,
        frames=frames,
        start_pos=start_pos,
        benchmark=benchmark,
        informative={},
        instrument_kinds={s: (kinds or {}).get(s, "equity") for s in frames},
        skipped=[],
        warnings=[],
    )


def _config(symbols, **overrides) -> BacktestConfig:
    base = dict(
        strategy="Scripted",
        params={},
        universe=UniverseSpec("list", symbols=tuple(symbols)),
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        init_cash=100_000.0,
        costs=ZERO,
        slippage_bps=0.0,
    )
    base.update(overrides)
    return BacktestConfig(**base)


def scripted(
    entries: dict[str, list[int]] | None = None,
    exits: dict[str, list[int]] | None = None,
    scores: dict[str, list[float]] | None = None,
    *,
    mode: str = "signal",
    **attrs,
) -> type[BasketStrategy]:
    entries = entries or {}
    exits = exits or {}
    scores = scores or {}

    class Scripted(BasketStrategy):
        name = "Scripted"

        def populate_entry(self, df, symbol, ctx):
            flags = np.zeros(len(df), dtype=bool)
            flags[entries.get(symbol, [])] = True
            df[ENTER] = flags
            df[ENTER_TAG] = np.where(flags, "script", None)
            return df

        def populate_exit(self, df, symbol, ctx):
            flags = np.zeros(len(df), dtype=bool)
            flags[exits.get(symbol, [])] = True
            df[EXIT] = flags
            df[EXIT_TAG] = np.where(flags, "script_exit", None)
            return df

        def populate_score(self, df, symbol, ctx):
            df[SCORE] = scores.get(symbol, [np.nan] * len(df))
            return df

    Scripted.mode = mode
    for key, value in attrs.items():
        setattr(Scripted, key, value)
    return Scripted


def _run(frames, strategy_cls, config=None, *, inputs=None, **config_overrides) -> BacktestResult:
    inputs = inputs or _inputs(frames)
    config = config or _config(list(frames), **config_overrides)
    return run_engine(inputs, strategy_cls, {}, config)


# ---------------------------------------------------------------- pure helpers


def test_roi_threshold_uses_the_largest_key_not_above_bars_held():
    roi = {0: 0.10, 3: 0.05, 10: 0.0}
    assert roi_threshold(roi, 0) == 0.10
    assert roi_threshold(roi, 2) == 0.10
    assert roi_threshold(roi, 3) == 0.05
    assert roi_threshold(roi, 11) == 0.0
    assert roi_threshold({5: 0.02}, 4) is None
    assert roi_threshold({}, 4) is None


def test_size_order_walks_shares_down_until_costs_fit_in_cash():
    assert size_order(100_000.0, 100.0, ZERO, 100_000.0) == 1000
    shares = size_order(100_000.0, 100.0, ZERODHA_DELIVERY, 100_000.0)
    assert shares < 1000
    assert shares * 100.0 + apply_costs("buy", shares, 100.0, ZERODHA_DELIVERY).total <= 100_000.0
    assert (shares + 1) * 100.0 + apply_costs("buy", shares + 1, 100.0, ZERODHA_DELIVERY).total > 100_000.0
    assert size_order(50_000.0, 100.0, ZERO, 100_000.0) == 500
    assert size_order(100_000.0, 100.0, ZERO, 20_000.0) == 200
    assert size_order(100_000.0, 100.0, ZERO, 100_000.0, max_shares=10) == 10
    assert size_order(50.0, 100.0, ZERO, 100_000.0) == 0
    assert size_order(1000.0, 0.0, ZERO, 1000.0) == 0


def test_rebalance_days_for_weekly_monthly_and_fixed_intervals():
    calendar = pd.bdate_range("2024-01-01", periods=30)
    assert is_rebalance_day(calendar, 0, 0, "M")
    assert is_rebalance_day(calendar, 0, 0, "W")
    assert is_rebalance_day(calendar, 0, 0, 7)
    monthly = [t for t in range(30) if is_rebalance_day(calendar, t, 0, "M")]
    assert monthly == [0, calendar.get_loc(pd.Timestamp("2024-02-01"))]
    weekly = [t for t in range(30) if is_rebalance_day(calendar, t, 0, "W")]
    assert weekly == [t for t in range(30) if calendar[t].weekday() == 0]
    every7 = [t for t in range(30) if is_rebalance_day(calendar, t, 3, 7)]
    assert every7 == [3, 10, 17, 24]
    assert not is_rebalance_day(calendar, 1, 3, 7)


# ---------------------------------------------------------------- fills and exits


def test_signal_on_bar_t_fills_at_next_open():
    frames = {"A": _frame([100, 100, 110, 120, 130, 140], opens=[100, 100, 105, 115, 125, 135])}
    result = _run(frames, scripted({"A": [1]}, {"A": [3]}, max_open_trades=1))
    trades = result.trades
    assert list(trades.columns) == TRADE_COLUMNS
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["open_date"] == frames["A"].index[2]
    assert trade["open_rate"] == 105.0
    assert trade["close_date"] == frames["A"].index[4]
    assert trade["close_rate"] == 125.0
    assert trade["exit_reason"] == "exit_signal"
    assert trade["enter_tag"] == "script"
    assert trade["exit_tag"] == "script_exit"
    assert trade["bars_held"] == 2
    assert trade["shares"] == 952
    assert trade["stake_amount"] == pytest.approx(952 * 105.0)
    assert trade["profit_abs"] == pytest.approx(952 * 20.0)
    assert trade["profit_ratio"] == pytest.approx(20.0 / 105.0)


def test_entry_is_ignored_when_the_same_bar_carries_an_exit_signal():
    frames = {"A": _frame([100] * 8)}
    result = _run(frames, scripted({"A": [1, 4]}, {"A": [1]}))
    assert len(result.trades) == 1
    assert result.trades.iloc[0]["open_date"] == frames["A"].index[5]


def test_exit_signal_beats_the_stop_on_the_same_bar():
    frames = {
        "A": _frame([100, 100, 100, 100, 80, 80], opens=[100, 100, 100, 100, 95, 80], lows=[100, 100, 100, 100, 80, 78])
    }
    result = _run(frames, scripted({"A": [1]}, {"A": [3]}, stoploss=-0.10))
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "exit_signal"
    assert trade["close_rate"] == 95.0


def test_gap_through_stop_fills_at_the_open():
    frames = {"A": _frame([100, 100, 100, 84, 84], opens=[100, 100, 100, 85, 84], lows=[100, 100, 100, 83, 83])}
    result = _run(frames, scripted({"A": [1]}, stoploss=-0.10))
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["close_rate"] == 85.0
    assert trade["close_date"] == frames["A"].index[3]


def test_stop_inside_the_bar_fills_at_the_stop_level():
    frames = {"A": _frame([100, 100, 100, 92, 92], opens=[100, 100, 100, 95, 92], lows=[100, 100, 100, 88, 90])}
    result = _run(frames, scripted({"A": [1]}, stoploss=-0.10))
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["close_rate"] == 90.0
    assert trade["min_rate"] == 100.0


def test_roi_exit_depends_on_bars_held():
    highs = [100, 100, 100, 108, 108, 106, 106, 106]
    frames = {"A": _frame([100] * 8, highs=highs)}
    result = _run(frames, scripted({"A": [1]}, minimal_roi={0: 0.10, 3: 0.05}))
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "roi"
    assert trade["close_date"] == frames["A"].index[5]
    assert trade["close_rate"] == 105.0
    assert trade["bars_held"] == 3


def test_roi_gap_above_target_fills_at_the_open():
    frames = {"A": _frame([100, 100, 100, 115, 115], opens=[100, 100, 100, 112, 115], highs=[100, 100, 100, 116, 116])}
    result = _run(frames, scripted({"A": [1]}, minimal_roi={0: 0.10}))
    assert result.trades.iloc[0]["close_rate"] == 112.0


def test_trailing_stop_arms_one_bar_after_the_high():
    frames = {
        "A": _frame(
            [100, 100, 115, 110, 110],
            opens=[100, 100, 100, 115, 110],
            highs=[100, 100, 120, 116, 111],
            lows=[100, 100, 100, 105, 108],
        )
    }
    strategy = scripted(
        {"A": [1]}, stoploss=-0.10, trailing_stop=True, trailing_stop_positive=0.05, trailing_stop_positive_offset=0.0
    )
    result = _run(frames, strategy)
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "trailing_stop_loss"
    assert trade["close_date"] == frames["A"].index[3]
    assert trade["close_rate"] == 114.0
    assert trade["max_rate"] == 120.0


def test_trailing_stop_waits_for_the_offset():
    frames = {
        "A": _frame(
            [100, 100, 103, 100, 100],
            opens=[100, 100, 100, 103, 100],
            highs=[100, 100, 104, 103, 101],
            lows=[100, 100, 100, 99, 99],
        )
    }
    strategy = scripted(
        {"A": [1]}, stoploss=-0.10, trailing_stop=True, trailing_stop_positive=0.01, trailing_stop_positive_offset=0.05
    )
    result = _run(frames, strategy)
    assert result.trades.iloc[0]["exit_reason"] == "force_exit"


def test_stop_beats_roi_on_the_same_bar():
    frames = {"A": _frame([100, 100, 100, 100], highs=[100, 100, 110, 100], lows=[100, 100, 85, 100])}
    result = _run(frames, scripted({"A": [0]}, stoploss=-0.10, minimal_roi={0: 0.05}))
    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["close_rate"] == 90.0


def test_use_exit_signal_false_ignores_exit_signals():
    frames = {"A": _frame([100] * 6)}
    result = _run(frames, scripted({"A": [1]}, {"A": [3]}, use_exit_signal=False))
    assert result.trades.iloc[0]["exit_reason"] == "force_exit"


# ---------------------------------------------------------------- sizing and slots


def test_max_open_trades_caps_entries_and_stake_is_cash_per_free_slot():
    frames = {s: _frame([100] * 6) for s in "ABC"}
    scores = {"A": [1.0] * 6, "B": [np.nan] * 6, "C": [5.0] * 6}
    strategy = scripted({s: [0] for s in "ABC"}, scores=scores, max_open_trades=2)
    result = _run(frames, strategy)
    assert set(result.trades["symbol"]) == {"A", "C"}
    assert result.trades["shares"].tolist() == [500, 500]
    assert result.open_positions.max() == 2
    ordered = _run(frames, strategy, stake_amount=60_000.0)
    by_symbol = ordered.trades.set_index("symbol")["shares"]
    assert by_symbol["C"] == 600
    assert by_symbol["A"] == 400


def test_fixed_stake_amount_sizes_every_order():
    frames = {s: _frame([200] * 6) for s in "AB"}
    result = _run(frames, scripted({s: [0] for s in "AB"}), stake_amount=10_000.0)
    assert result.trades["shares"].tolist() == [50, 50]


def test_cash_never_negative_and_equity_is_cash_plus_invested():
    rng = np.random.default_rng(7)
    frames = {}
    for s in "ABCDE":
        closes = 100 * np.cumprod(1 + rng.normal(0, 0.02, 40))
        frames[s] = _frame([round(c, 2) for c in closes])
    entries = {s: list(range(0, 40, 5)) for s in "ABCDE"}
    exits = {s: list(range(3, 40, 7)) for s in "ABCDE"}
    result = _run(frames, scripted(entries, exits, max_open_trades=3), costs=ZERODHA_DELIVERY, slippage_bps=5.0)
    assert (result.cash >= 0).all()
    assert np.allclose(result.equity, result.cash + result.invested)
    assert len(result.trades) > 3
    assert (result.trades["entry_costs"] > 0).all()
    assert (result.trades["exit_costs"] > 0).all()
    assert result.equity.index.equals(result.cash.index)
    assert result.equity.index.equals(frames["A"].index)


def test_volume_share_cap_limits_shares():
    frames = {"A": _frame([100] * 6, volume=1000)}
    result = _run(frames, scripted({"A": [0]}), max_volume_share=0.1)
    assert result.trades.iloc[0]["shares"] == 100


def test_slippage_and_costs_flow_into_profit():
    frames = {"A": _frame([1000] * 6)}
    result = _run(frames, scripted({"A": [0]}, {"A": [2]}), costs=ZERODHA_DELIVERY, slippage_bps=10.0)
    trade = result.trades.iloc[0]
    assert trade["open_rate"] == 1001.0
    assert trade["close_rate"] == 999.0
    shares = trade["shares"]
    entry = apply_costs("buy", shares, 1001.0, ZERODHA_DELIVERY).total
    exit_ = apply_costs("sell", shares, 999.0, ZERODHA_DELIVERY).total
    assert trade["entry_costs"] == pytest.approx(entry)
    assert trade["exit_costs"] == pytest.approx(exit_)
    assert trade["profit_abs"] == pytest.approx(shares * -2.0 - entry - exit_)
    assert trade["profit_ratio"] == pytest.approx(trade["profit_abs"] / (shares * 1001.0 + entry))


def test_etf_legs_are_charged_with_the_etf_stt():
    frames = {"A": _frame([1000] * 6), "B": _frame([1000] * 6)}
    inputs = _inputs(frames, kinds={"A": "etf"})
    config = _config(["A", "B"], costs=ZERODHA_DELIVERY, init_cash=1_000_000.0, stake_amount=100_000.0)
    result = run_engine(inputs, scripted({"A": [0], "B": [0]}), {}, config)
    by_symbol = result.trades.set_index("symbol")
    assert by_symbol.loc["A", "shares"] == by_symbol.loc["B", "shares"]
    assert by_symbol.loc["A", "entry_costs"] < by_symbol.loc["B", "entry_costs"] - 90


# ---------------------------------------------------------------- rank mode


def test_rank_rebalance_buys_top_k_and_drops_below_exit_rank():
    n = 6
    frames = {s: _frame([100] * n) for s in "ABCD"}
    scores = {
        "A": [4.0, 3.0, 3.0, 3.0, 3.0, 3.0],
        "B": [3.0, 0.5, 0.5, 0.5, 0.5, 0.5],
        "C": [2.0, 4.0, 4.0, 4.0, 4.0, 4.0],
        "D": [1.0, 2.0, 2.0, 2.0, 2.0, 2.0],
    }
    strategy = scripted(scores=scores, mode="rank", top_k=2, exit_rank=3, rebalance_every=1)
    inputs = _inputs(frames, start_pos=1)
    result = run_engine(inputs, strategy, {}, _config(list(frames)))
    trades = result.trades.sort_values(["open_date", "symbol"])
    opened_day1 = trades[trades["open_date"] == frames["A"].index[1]]
    assert opened_day1["symbol"].tolist() == ["A", "B"]
    assert opened_day1["stake_amount"].tolist() == [50_000.0, 50_000.0]
    opened_day2 = trades[trades["open_date"] == frames["A"].index[2]]
    assert opened_day2["symbol"].tolist() == ["C"]
    b_trade = trades[trades["symbol"] == "B"].iloc[0]
    assert b_trade["exit_reason"] == "rank_drop"
    assert b_trade["close_date"] == frames["A"].index[2]
    assert "D" not in set(trades["symbol"])
    assert (trades[trades["symbol"] != "B"]["exit_reason"] == "force_exit").all()
    assert len(trades[trades["symbol"] == "A"]) == 1


def test_rank_mode_drops_positions_whose_score_turns_nan():
    frames = {s: _frame([100] * 5) for s in "AB"}
    scores = {"A": [2.0, np.nan, np.nan, np.nan, np.nan], "B": [1.0, 1.0, 1.0, 1.0, 1.0]}
    strategy = scripted(scores=scores, mode="rank", top_k=2, exit_rank=2, rebalance_every=1)
    result = run_engine(_inputs(frames, start_pos=1), strategy, {}, _config(list(frames)))
    a_trade = result.trades[result.trades["symbol"] == "A"].iloc[0]
    assert a_trade["exit_reason"] == "rank_drop"
    assert a_trade["close_date"] == frames["A"].index[2]


def test_rank_stake_is_capped_by_cash():
    frames = {s: _frame([100] * 4) for s in "ABC"}
    scores = {"A": [3.0] * 4, "B": [2.0] * 4, "C": [1.0] * 4}
    strategy = scripted(scores=scores, mode="rank", top_k=2, exit_rank=5, rebalance_every=1)
    result = run_engine(_inputs(frames, start_pos=1), strategy, {}, _config(list(frames), init_cash=60_000.0))
    trades = result.trades.sort_values("open_date")
    assert trades["stake_amount"].tolist() == [30_000.0, 30_000.0]
    assert (result.cash >= 0).all()


def test_rank_mode_only_rebalances_on_rebalance_days():
    frames = {s: _frame([100] * 8) for s in "AB"}
    scores = {"A": [2.0] * 8, "B": [1.0] * 8}
    strategy = scripted(scores=scores, mode="rank", top_k=1, exit_rank=1, rebalance_every=5)
    result = run_engine(_inputs(frames, start_pos=1), strategy, {}, _config(list(frames)))
    assert result.trades["symbol"].tolist() == ["A"]
    assert result.trades.iloc[0]["open_date"] == frames["A"].index[1]


# ---------------------------------------------------------------- end of data


def test_force_exit_on_the_final_bar_and_delisting_at_the_last_close():
    a = _frame([100, 100, 100, 100, 100, 130])
    b = _frame([100, 100, 100, 120, np.nan, np.nan])
    b.loc[b.index[4:], ["Open", "High", "Low"]] = np.nan
    frames = {"A": a, "B": b}
    result = _run(frames, scripted({"A": [0], "B": [0]}))
    by_symbol = result.trades.set_index("symbol")
    assert by_symbol.loc["A", "exit_reason"] == "force_exit"
    assert by_symbol.loc["A", "close_rate"] == 130.0
    assert by_symbol.loc["A", "close_date"] == a.index[-1]
    assert by_symbol.loc["B", "exit_reason"] == "delisted"
    assert by_symbol.loc["B", "close_rate"] == 120.0
    assert by_symbol.loc["B", "close_date"] == b.index[3]
    assert result.open_positions.iloc[-1] == 0
    assert result.invested.iloc[-1] == 0
    assert result.equity.iloc[-1] == pytest.approx(result.cash.iloc[-1])


def test_nan_open_leaves_the_trade_untouched_and_carries_the_last_close():
    a = _frame([100, 100, 100, np.nan, 100, 100, 100])
    a.loc[a.index[3], ["Open", "High", "Low"]] = np.nan
    frames = {"A": a}
    result = _run(frames, scripted({"A": [0]}, {"A": [2, 4]}, stoploss=-0.01))
    trade = result.trades.iloc[0]
    assert trade["close_date"] == a.index[5]
    assert trade["exit_reason"] == "exit_signal"
    assert result.open_positions.iloc[3] == 1
    assert result.invested.iloc[3] == pytest.approx(result.invested.iloc[2])


def test_entry_skipped_when_the_open_is_nan():
    a = _frame([100, 100, 100, 100])
    a.loc[a.index[1], ["Open", "High", "Low", "Close"]] = np.nan
    result = _run({"A": a}, scripted({"A": [0, 2]}))
    assert len(result.trades) == 1
    assert result.trades.iloc[0]["open_date"] == a.index[3]


# ---------------------------------------------------------------- result shape


def test_result_series_start_at_start_pos_and_returns_chain_from_init_cash():
    frames = {"A": _frame([100, 100, 110, 121, 121, 121])}
    inputs = _inputs(frames, start_pos=1)
    result = run_engine(inputs, scripted({"A": [1]}, max_open_trades=1), {}, _config(["A"]))
    assert result.equity.index.equals(frames["A"].index[1:])
    assert result.returns.iloc[0] == pytest.approx(result.equity.iloc[0] / 100_000.0 - 1)
    assert result.returns.iloc[1] == pytest.approx(0.0)
    assert result.returns.iloc[2] == pytest.approx(0.1, abs=1e-3)
    assert result.exposure.iloc[2] == pytest.approx(result.invested.iloc[2] / result.equity.iloc[2])
    assert result.strategy_name == "Scripted"
    assert result.params == {}
    assert result.config is not None
    assert result.metrics["total_trades"] == 1
    assert result.metrics["final_equity"] == pytest.approx(result.equity.iloc[-1])
    assert set(result.per_symbol["symbol"]) == {"A"}
    assert set(result.per_exit_reason["exit_reason"]) == {"force_exit"}


def test_benchmark_returns_align_with_the_equity_index():
    frames = {"A": _frame([100] * 6)}
    bench = _frame([100, 101, 102, 103, 104, 105])
    inputs = _inputs(frames, start_pos=2, benchmark=bench)
    result = run_engine(inputs, scripted(), {}, _config(["A"]))
    assert result.benchmark_returns.index.equals(frames["A"].index[2:])
    assert result.benchmark_returns.iloc[1] == pytest.approx(103 / 102 - 1)
    assert result.trades.empty
    assert list(result.trades.columns) == TRADE_COLUMNS


def test_empty_run_has_flat_equity_and_no_trades():
    frames = {"A": _frame([100] * 5)}
    result = _run(frames, scripted())
    assert (result.equity == 100_000.0).all()
    assert result.trades.empty
    assert result.metrics["total_trades"] == 0
    assert result.per_symbol.empty
    assert result.per_exit_reason.empty


def test_strategy_overrides_and_progress_and_intraday_warning():
    frames = {"A": _frame([100, 100, 100, 85, 85] + [85] * 60, lows=[100, 100, 100, 84, 84] + [85] * 60)}
    calls: list[dict] = []
    config = _config(["A"], strategy_overrides={"stoploss": -0.05}, costs=ZERODHA_INTRADAY)
    result = run_engine(_inputs(frames), scripted({"A": [1]}), {}, config, progress_cb=lambda **f: calls.append(f))
    assert result.trades.iloc[0]["exit_reason"] == "stop_loss"
    assert result.trades.iloc[0]["close_rate"] == 85.0
    phases = {c.get("phase") for c in calls}
    assert "Simulating" in phases
    assert "Signals" in phases
    assert any("intraday" in w.lower() for w in result.warnings)


def test_strategy_params_reach_the_instance_and_diagnostics_are_captured():
    from strategies.parameters import IntParameter

    class Param(scripted({"A": [0]})):
        name = "Param"
        lag = IntParameter(3, 1, 10)

        def diagnostics(self):
            return {"lag": self.lag}

    frames = {"A": _frame([100] * 4)}
    result = run_engine(_inputs(frames), Param, {"lag": 7}, _config(["A"]))
    assert result.diagnostics == {"lag": 7}
    assert result.params == {"lag": 7}


def test_strategies_never_see_fills_and_indicators_run_on_a_copy():
    frames = {"A": _frame([100] * 6)}
    seen: dict[str, pd.DataFrame] = {}

    class Peek(scripted({"A": [0]})):
        name = "Peek"

        def populate_indicators(self, df, symbol, ctx):
            seen[symbol] = df
            df["sma"] = df["Close"].rolling(2).mean()
            return df

    run_engine(_inputs(frames), Peek, {}, _config(["A"]))
    assert "sma" not in frames["A"].columns
    assert "sma" in seen["A"].columns


def test_truncating_future_bars_does_not_change_earlier_trades_or_equity():
    rng = np.random.default_rng(3)
    closes = [round(c, 2) for c in 100 * np.cumprod(1 + rng.normal(0, 0.02, 60))]

    class Crossover(BasketStrategy):
        name = "Crossover"

        def populate_entry(self, df, symbol, ctx):
            fast = df["Close"].rolling(3).mean()
            slow = df["Close"].rolling(8).mean()
            df[ENTER] = (fast > slow) & (fast.shift(1) <= slow.shift(1))
            return df

        def populate_exit(self, df, symbol, ctx):
            fast = df["Close"].rolling(3).mean()
            slow = df["Close"].rolling(8).mean()
            df[EXIT] = (fast < slow) & (fast.shift(1) >= slow.shift(1))
            return df

    full = _frame(closes)
    cut = 40
    truncated = full.iloc[:cut]
    result_full = run_engine(_inputs({"A": full}, start_pos=8), Crossover, {}, _config(["A"]))
    result_cut = run_engine(_inputs({"A": truncated}, start_pos=8), Crossover, {}, _config(["A"]))
    pd.testing.assert_series_equal(result_full.equity.iloc[: cut - 9], result_cut.equity.iloc[: cut - 9])
    closed_full = result_full.trades[result_full.trades["close_date"] <= full.index[cut - 1]].reset_index(drop=True)
    closed_cut = result_cut.trades[result_cut.trades["exit_reason"] != "force_exit"].reset_index(drop=True)
    pd.testing.assert_frame_equal(closed_full, closed_cut)
