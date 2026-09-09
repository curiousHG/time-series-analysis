"""Basket backtest engine: a daily loop over (symbols × bars) with next-open fills.

Per bar `t` from `start_pos`, in this order: exits at Open[t] from yesterday's signals (or a
rank drop on a rebalance day); the intrabar ladder for trades opened before `t` (stop, then ROI,
then the trailing level for `t+1`, then max/min tracking); entries at Open[t] from yesterday's
signals or ranks; delisting and final-bar force exits at Close[t]; mark to market at Close[t].

The entry bar contributes to a trade's max/min tracking and trailing level but is never checked
for a stop or ROI. Fills go through `fill_price` (slippage plus tick rounding) and `apply_costs`
per instrument kind. `size_order` keeps cash non-negative, so `equity == cash + invested` always.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from services.backtest import metrics as result_metrics
from services.backtest.costs import apply_costs, fill_price
from strategies.basket import ENTER, ENTER_TAG, EXIT, EXIT_TAG, SCORE, MarketContext, finalize_signals

if TYPE_CHECKING:
    from collections.abc import Callable

    from services.backtest.config import BacktestConfig
    from services.backtest.costs import CostPreset
    from services.backtest.universe import BasketInputs
    from strategies.basket import BasketStrategy

logger = logging.getLogger(__name__)

TRADE_COLUMNS = [
    "symbol",
    "open_date",
    "close_date",
    "open_rate",
    "close_rate",
    "shares",
    "stake_amount",
    "entry_costs",
    "exit_costs",
    "profit_abs",
    "profit_ratio",
    "exit_reason",
    "enter_tag",
    "exit_tag",
    "bars_held",
    "max_rate",
    "min_rate",
]
_TRADE_DTYPES = {
    "symbol": object,
    "open_date": "datetime64[ns]",
    "close_date": "datetime64[ns]",
    "open_rate": float,
    "close_rate": float,
    "shares": int,
    "stake_amount": float,
    "entry_costs": float,
    "exit_costs": float,
    "profit_abs": float,
    "profit_ratio": float,
    "exit_reason": object,
    "enter_tag": object,
    "exit_tag": object,
    "bars_held": int,
    "max_rate": float,
    "min_rate": float,
}
PROGRESS_EVERY = 50
INTRADAY_PRESET = "zerodha_intraday"


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series
    cash: pd.Series
    invested: pd.Series
    exposure: pd.Series
    open_positions: pd.Series
    returns: pd.Series
    benchmark_returns: pd.Series | None
    per_symbol: pd.DataFrame
    per_exit_reason: pd.DataFrame
    metrics: dict = field(default_factory=dict)
    diagnostics: Any | None = None
    skipped_symbols: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    config: BacktestConfig | None = None
    strategy_name: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class _Position:
    symbol: str
    index: int
    open_pos: int
    open_rate: float
    shares: int
    entry_costs: float
    enter_tag: str | None
    max_rate: float
    min_rate: float
    stop_rate: float
    trailing: bool = False

    @property
    def stake(self) -> float:
        return self.shares * self.open_rate


@dataclass
class _Signals:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    enter: np.ndarray
    exit: np.ndarray
    score: np.ndarray
    enter_tag: np.ndarray
    exit_tag: np.ndarray
    last_close_pos: np.ndarray


# ---------------------------------------------------------------- pure helpers


def roi_threshold(minimal_roi: dict[int, float], bars_held: int) -> float | None:
    """The ROI target in force after `bars_held` bars: the value at the largest key not above it."""
    eligible = [k for k in minimal_roi if k <= bars_held]
    if not eligible:
        return None
    return float(minimal_roi[max(eligible)])


def size_order(
    stake: float,
    price: float,
    preset: CostPreset,
    cash: float,
    *,
    instrument: str = "equity",
    max_shares: int | None = None,
) -> int:
    """Whole shares for `stake` at `price`, walked down until shares × price + entry costs fit in
    `cash`, so a fill never takes cash negative."""
    if not np.isfinite(price) or price <= 0:
        return 0
    shares = int(min(stake, cash) // price)
    if max_shares is not None:
        shares = min(shares, int(max_shares))
    while shares > 0 and shares * price + apply_costs("buy", shares, price, preset, instrument=instrument).total > cash:
        shares -= 1
    return max(shares, 0)


def is_rebalance_day(calendar: pd.DatetimeIndex, t: int, start_pos: int, every: int | str) -> bool:
    """The first trading bar, then the first bar of each new ISO week / month, or every N bars."""
    if t == start_pos:
        return True
    if t < start_pos:
        return False
    previous, current = calendar[t - 1], calendar[t]
    if every == "W":
        return previous.isocalendar()[:2] != current.isocalendar()[:2]
    if every == "M":
        return (previous.year, previous.month) != (current.year, current.month)
    n = int(every)
    return n > 0 and (t - start_pos) % n == 0


def rank_scores(scores: np.ndarray) -> np.ndarray:
    """1-based rank by descending score, ties broken by array order; NaN scores get rank 0."""
    ranks = np.zeros(len(scores), dtype=int)
    valid = np.flatnonzero(~np.isnan(scores))
    ordered = valid[np.argsort(-scores[valid], kind="stable")]
    ranks[ordered] = np.arange(1, len(ordered) + 1)
    return ranks


def evaluate_ladder(
    position: _Position, open_: float, high: float, low: float, bars_held: int, minimal_roi: dict[int, float]
) -> tuple[str, float] | None:
    """(exit reason, raw fill price) when the stop or an ROI target triggers on this bar."""
    if low <= position.stop_rate:
        reason = "trailing_stop_loss" if position.trailing else "stop_loss"
        return reason, min(open_, position.stop_rate)
    roi = roi_threshold(minimal_roi, bars_held)
    if roi is not None:
        target = position.open_rate * (1 + roi)
        if high >= target:
            return "roi", max(open_, target)
    return None


def track_bar(position: _Position, high: float, low: float, strategy: BasketStrategy) -> None:
    """Update max/min rates and, when trailing is on and the offset is cleared, raise the stop
    to `trailing_stop_positive` below the high — in force from the next bar."""
    position.max_rate = max(position.max_rate, high)
    position.min_rate = min(position.min_rate, low)
    if not strategy.trailing_stop:
        return
    if position.max_rate / position.open_rate - 1 > strategy.trailing_stop_positive_offset:
        candidate = position.max_rate * (1 - strategy.trailing_stop_positive)
        if candidate > position.stop_rate:
            position.stop_rate = candidate
            position.trailing = True


# ---------------------------------------------------------------- signals


def _compute_signals(strategy, inputs: BasketInputs, ctx: MarketContext, progress_cb) -> _Signals:
    symbols = list(inputs.frames)
    total = len(symbols)
    frames: dict[str, pd.DataFrame] = {}
    for i, symbol in enumerate(symbols):
        frames[symbol] = strategy.populate_indicators(inputs.frames[symbol].copy(), symbol, ctx)
        if i % PROGRESS_EVERY == 0:
            _report(progress_cb, phase="Signals", done=i, total=total)
    frames = strategy.prepare_basket(frames, progress_cb=progress_cb)
    calendar = inputs.calendar
    shape = (total, len(calendar))
    arrays = {name: np.full(shape, np.nan) for name in ("open", "high", "low", "close", "volume", "score")}
    enter = np.zeros(shape, dtype=bool)
    exit_ = np.zeros(shape, dtype=bool)
    enter_tag = np.full(shape, None, dtype=object)
    exit_tag = np.full(shape, None, dtype=object)
    for i, symbol in enumerate(symbols):
        df = frames[symbol]
        df = strategy.populate_entry(df, symbol, ctx)
        df = strategy.populate_exit(df, symbol, ctx)
        df = strategy.populate_score(df, symbol, ctx)
        df = finalize_signals(df.reindex(calendar), mode=strategy.mode)
        for name, column in (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")):
            arrays[name][i] = df[column].to_numpy(dtype=float)
        if "Volume" in df.columns:
            arrays["volume"][i] = pd.to_numeric(df["Volume"], errors="coerce").to_numpy(dtype=float)
        arrays["score"][i] = df[SCORE].to_numpy(dtype=float)
        enter[i] = df[ENTER].to_numpy(dtype=bool)
        exit_[i] = df[EXIT].to_numpy(dtype=bool)
        enter_tag[i] = df[ENTER_TAG].to_numpy(dtype=object)
        exit_tag[i] = df[EXIT_TAG].to_numpy(dtype=object)
    _report(progress_cb, phase="Signals", done=total, total=total)
    valid_close = ~np.isnan(arrays["close"])
    last_close_pos = np.where(valid_close.any(axis=1), shape[1] - 1 - np.argmax(valid_close[:, ::-1], axis=1), -1)
    return _Signals(
        open=arrays["open"],
        high=arrays["high"],
        low=arrays["low"],
        close=arrays["close"],
        volume=arrays["volume"],
        enter=enter,
        exit=exit_,
        score=arrays["score"],
        enter_tag=enter_tag,
        exit_tag=exit_tag,
        last_close_pos=last_close_pos,
    )


# ---------------------------------------------------------------- the loop


class _Book:
    """Cash, open positions and closed trades for one run; owns the fill arithmetic."""

    def __init__(self, config: BacktestConfig, symbols: list[str], kinds: dict[str, str], calendar):
        self.config = config
        self.symbols = symbols
        self.kinds = kinds
        self.calendar = calendar
        self.cash = float(config.init_cash)
        self.positions: dict[int, _Position] = {}
        self.trades: list[dict] = []

    def kind(self, index: int) -> str:
        return self.kinds.get(self.symbols[index], "equity")

    def open(self, index: int, t: int, raw_price: float, stake: float, tag, stoploss: float, volume: float) -> bool:
        price = fill_price(raw_price, "buy", self.config.slippage_bps)
        max_shares = None
        if self.config.max_volume_share is not None and np.isfinite(volume):
            max_shares = int(volume * self.config.max_volume_share)
        shares = size_order(
            stake, price, self.config.costs, self.cash, instrument=self.kind(index), max_shares=max_shares
        )
        if shares <= 0:
            return False
        costs = apply_costs("buy", shares, price, self.config.costs, instrument=self.kind(index)).total
        self.cash -= shares * price + costs
        self.positions[index] = _Position(
            symbol=self.symbols[index],
            index=index,
            open_pos=t,
            open_rate=price,
            shares=shares,
            entry_costs=costs,
            enter_tag=tag if isinstance(tag, str) else None,
            max_rate=price,
            min_rate=price,
            stop_rate=price * (1 + stoploss),
        )
        return True

    def close(self, position: _Position, t: int, raw_price: float, reason: str, tag=None) -> None:
        price = fill_price(raw_price, "sell", self.config.slippage_bps)
        costs = apply_costs(
            "sell", position.shares, price, self.config.costs, instrument=self.kind(position.index)
        ).total
        self.cash += position.shares * price - costs
        profit_abs = position.shares * (price - position.open_rate) - position.entry_costs - costs
        self.trades.append(
            {
                "symbol": position.symbol,
                "open_date": self.calendar[position.open_pos],
                "close_date": self.calendar[t],
                "open_rate": position.open_rate,
                "close_rate": price,
                "shares": position.shares,
                "stake_amount": position.stake,
                "entry_costs": position.entry_costs,
                "exit_costs": costs,
                "profit_abs": profit_abs,
                "profit_ratio": profit_abs / (position.stake + position.entry_costs),
                "exit_reason": reason,
                "enter_tag": position.enter_tag,
                "exit_tag": tag if isinstance(tag, str) else None,
                "bars_held": t - position.open_pos,
                "max_rate": position.max_rate,
                "min_rate": position.min_rate,
            }
        )
        del self.positions[position.index]


def run_engine(
    inputs: BasketInputs,
    strategy_cls: type[BasketStrategy],
    params: dict,
    config: BacktestConfig,
    *,
    progress_cb: Callable[..., None] | None = None,
) -> BacktestResult:
    strategy = strategy_cls(**params)
    strategy.apply_overrides(config.strategy_overrides)
    symbols = list(inputs.frames)
    calendar = inputs.calendar
    ctx = MarketContext(
        calendar=calendar, universe=tuple(symbols), benchmark=inputs.benchmark, informative=dict(inputs.informative)
    )
    sig = _compute_signals(strategy, inputs, ctx, progress_cb)
    book = _Book(config, symbols, inputs.instrument_kinds, calendar)

    n_symbols, n_bars = sig.close.shape
    start_pos = max(0, min(inputs.start_pos, n_bars))
    span = n_bars - start_pos
    equity = np.zeros(span)
    cash = np.zeros(span)
    invested = np.zeros(span)
    open_count = np.zeros(span, dtype=int)
    last_price = np.full(n_symbols, np.nan)
    rank_mode = strategy.mode == "rank"
    exit_rank = strategy.exit_rank if strategy.exit_rank is not None else strategy.top_k

    for t in range(start_pos, n_bars):
        rebalance = rank_mode and is_rebalance_day(calendar, t, start_pos, strategy.rebalance_every)
        ranks_prev = rank_scores(sig.score[:, t - 1]) if rebalance and t > 0 else None

        for position in list(book.positions.values()):
            i = position.index
            open_ = sig.open[i, t]
            if np.isnan(open_):
                continue
            if strategy.use_exit_signal and t > 0 and sig.exit[i, t - 1]:
                book.close(position, t, open_, "exit_signal", tag=sig.exit_tag[i, t - 1])
            elif ranks_prev is not None and (ranks_prev[i] == 0 or ranks_prev[i] > exit_rank):
                book.close(position, t, open_, "rank_drop")

        for position in list(book.positions.values()):
            i = position.index
            open_, high, low = sig.open[i, t], sig.high[i, t], sig.low[i, t]
            if position.open_pos >= t or np.isnan(open_) or np.isnan(high) or np.isnan(low):
                continue
            hit = evaluate_ladder(position, open_, high, low, t - position.open_pos, strategy.minimal_roi)
            if hit is not None:
                book.close(position, t, hit[1], hit[0])
                continue
            track_bar(position, high, low, strategy)

        if rank_mode:
            if ranks_prev is not None:
                _rank_entries(book, sig, strategy, ranks_prev, t, last_price)
        else:
            _signal_entries(book, sig, strategy, t)

        for position in list(book.positions.values()):
            i = position.index
            close = sig.close[i, t]
            if t == n_bars - 1:
                book.close(position, t, close if not np.isnan(close) else last_price[i], "force_exit")
            elif t == sig.last_close_pos[i]:
                book.close(position, t, close, "delisted")

        valid = ~np.isnan(sig.close[:, t])
        last_price[valid] = sig.close[valid, t]
        held = sum(p.shares * last_price[p.index] for p in book.positions.values())
        k = t - start_pos
        cash[k] = book.cash
        invested[k] = held
        equity[k] = book.cash + held
        open_count[k] = len(book.positions)
        if k % PROGRESS_EVERY == 0 or t == n_bars - 1:
            _report(progress_cb, phase="Simulating", done=k + 1, total=span)

    return _build_result(book, inputs, config, strategy, params, equity, cash, invested, open_count, start_pos)


def _signal_entries(book: _Book, sig: _Signals, strategy, t: int) -> None:
    free = strategy.max_open_trades - len(book.positions)
    if free <= 0 or t == 0:
        return
    n_symbols = sig.close.shape[0]
    candidates = [
        i
        for i in range(n_symbols)
        if i not in book.positions and sig.enter[i, t - 1] and not sig.exit[i, t - 1] and not np.isnan(sig.open[i, t])
    ]
    prev_score = sig.score[:, t - 1]
    candidates.sort(key=lambda i: (np.inf if np.isnan(prev_score[i]) else -prev_score[i], i))
    for i in candidates:
        if free <= 0:
            break
        stake = book.config.stake_amount if book.config.stake_amount is not None else book.cash / free
        if book.open(i, t, sig.open[i, t], stake, sig.enter_tag[i, t - 1], strategy.stoploss, sig.volume[i, t]):
            track_bar(book.positions[i], sig.high[i, t], sig.low[i, t], strategy)
            free -= 1


def _rank_entries(book: _Book, sig: _Signals, strategy, ranks_prev: np.ndarray, t: int, last_price) -> None:
    top = [i for i in np.argsort(ranks_prev, kind="stable") if 0 < ranks_prev[i] <= strategy.top_k]
    if not top:
        return
    held_value = 0.0
    for position in book.positions.values():
        price = sig.open[position.index, t]
        held_value += position.shares * (last_price[position.index] if np.isnan(price) else price)
    portfolio_value = book.cash + held_value
    weights = _rank_weights(strategy, sig.score[:, t - 1], top, portfolio_value)
    for i in top:
        if i in book.positions or np.isnan(sig.open[i, t]) or sig.exit[i, t - 1]:
            continue
        stake = min(weights[i], book.cash)
        if book.open(i, t, sig.open[i, t], stake, sig.enter_tag[i, t - 1], strategy.stoploss, sig.volume[i, t]):
            track_bar(book.positions[i], sig.high[i, t], sig.low[i, t], strategy)


def _rank_weights(strategy, scores: np.ndarray, top: list[int], portfolio_value: float) -> dict[int, float]:
    equal = portfolio_value / strategy.top_k
    if strategy.rank_weights != "score":
        return dict.fromkeys(top, equal)
    positive = {i: max(float(scores[i]), 0.0) for i in top}
    total = sum(positive.values())
    if total <= 0:
        return dict.fromkeys(top, equal)
    return {i: portfolio_value * positive[i] / total for i in top}


# ---------------------------------------------------------------- result assembly


def _trades_frame(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame({c: pd.Series(dtype=d) for c, d in _TRADE_DTYPES.items()})[TRADE_COLUMNS]
    return pd.DataFrame(records, columns=TRADE_COLUMNS)


def _build_result(book, inputs, config, strategy, params, equity, cash, invested, open_count, start_pos):
    index = inputs.calendar[start_pos:]
    equity_s = pd.Series(equity, index=index, name="equity")
    cash_s = pd.Series(cash, index=index, name="cash")
    invested_s = pd.Series(invested, index=index, name="invested")
    exposure = (invested_s / equity_s.replace(0.0, np.nan)).fillna(0.0).rename("exposure")
    previous = equity_s.shift(1).fillna(float(config.init_cash))
    returns = (equity_s / previous - 1).rename("returns")
    trades = _trades_frame(book.trades)
    warnings = list(inputs.warnings)
    if config.costs.name == INTRADAY_PRESET:
        warnings.append("Intraday cost preset applied to overnight positions — for cost comparison only.")
    result = BacktestResult(
        trades=trades,
        equity=equity_s,
        cash=cash_s,
        invested=invested_s,
        exposure=exposure,
        open_positions=pd.Series(open_count, index=index, name="open_positions"),
        returns=returns,
        benchmark_returns=_benchmark_returns(inputs.benchmark, inputs.calendar, start_pos),
        per_symbol=result_metrics.per_symbol_stats(trades),
        per_exit_reason=result_metrics.per_exit_reason_stats(trades),
        diagnostics=strategy.diagnostics(),
        skipped_symbols=list(inputs.skipped),
        warnings=warnings,
        config=config,
        strategy_name=strategy.name,
        params=strategy.params_dict(),
    )
    result.metrics = result_metrics.summarize_result(result)
    logger.info(
        "backtest %s: %d trade(s), final equity %.2f", strategy.name, len(trades), result.metrics["final_equity"]
    )
    return result


def _benchmark_returns(benchmark: pd.DataFrame | None, calendar, start_pos: int) -> pd.Series | None:
    if benchmark is None or "Close" not in benchmark.columns:
        return None
    close = pd.to_numeric(benchmark["Close"], errors="coerce").reindex(calendar).ffill()
    if close.isna().all():
        return None
    return close.pct_change().iloc[start_pos:].fillna(0.0).rename("benchmark_returns")


def _report(progress_cb: Callable[..., None] | None, **fields: Any) -> None:
    if progress_cb is not None:
        progress_cb(**fields)
