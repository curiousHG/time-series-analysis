"""Summaries of a basket backtest: headline metrics, per-symbol and per-exit-reason splits,
periodic breakdowns and drawdown detail. Equity-curve statistics reuse `compute_metrics`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from services.backtest_service import compute_metrics
from services.constants import TRADING_DAYS

if TYPE_CHECKING:
    from services.backtest.engine import BacktestResult

LEGACY_COLUMNS = ["Return", "PnL", "Entry Timestamp", "Exit Timestamp"]
PERIOD_RULES = {"D": "D", "W": "W", "M": "ME", "Y": "YE"}
SYMBOL_STATS_COLUMNS = [
    "symbol",
    "trades",
    "wins",
    "win_rate",
    "profit_abs",
    "avg_profit_ratio",
    "costs",
    "avg_bars_held",
]
REASON_STATS_COLUMNS = ["exit_reason", "trades", "wins", "win_rate", "profit_abs", "avg_profit_ratio", "avg_bars_held"]


def trades_to_legacy(trades: pd.DataFrame) -> pd.DataFrame:
    """The engine's trade frame in the shape `compute_metrics` reads."""
    return pd.DataFrame(
        {
            "Return": trades["profit_ratio"].to_numpy(dtype=float),
            "PnL": trades["profit_abs"].to_numpy(dtype=float),
            "Entry Timestamp": pd.to_datetime(trades["open_date"]),
            "Exit Timestamp": pd.to_datetime(trades["close_date"]),
        },
        columns=LEGACY_COLUMNS,
    )


def summarize_result(result: BacktestResult) -> dict:
    trades, equity, returns = result.trades, result.equity, result.returns
    metrics: dict = {}
    if len(returns) >= 2:
        metrics.update(compute_metrics(returns, trades_to_legacy(trades)))
    init_cash = float(result.config.init_cash) if result.config is not None else float(equity.iloc[0])
    final_equity = float(equity.iloc[-1]) if len(equity) else init_cash
    total_return_pct = (final_equity / init_cash - 1) * 100 if init_cash else 0.0
    entry_costs = float(trades["entry_costs"].sum()) if len(trades) else 0.0
    exit_costs = float(trades["exit_costs"].sum()) if len(trades) else 0.0
    wins, losses = consecutive_streaks(trades["profit_abs"]) if len(trades) else (0, 0)
    drawdown = drawdown_stats(equity)
    benchmark = _benchmark_summary(returns, result.benchmark_returns, total_return_pct)
    metrics.update(
        {
            "total_trades": int(len(trades)),
            "final_equity": final_equity,
            "total_profit_abs": final_equity - init_cash,
            "total_return_pct": total_return_pct,
            "total_costs": entry_costs + exit_costs,
            "entry_costs": entry_costs,
            "exit_costs": exit_costs,
            "avg_exposure": float(result.exposure.mean() * 100) if len(result.exposure) else 0.0,
            "max_open_positions": int(result.open_positions.max()) if len(result.open_positions) else 0,
            "consecutive_wins": wins,
            "consecutive_losses": losses,
            "best_day_pct": float(returns.max() * 100) if len(returns) else 0.0,
            "worst_day_pct": float(returns.min() * 100) if len(returns) else 0.0,
            "max_drawdown_abs": drawdown["max_drawdown_abs"],
            "drawdown_start": drawdown["drawdown_start"],
            "drawdown_end": drawdown["drawdown_end"],
            "drawdown_days": drawdown["drawdown_days"],
            "symbols_traded": int(trades["symbol"].nunique()) if len(trades) else 0,
            **benchmark,
        }
    )
    return metrics


def _benchmark_summary(returns: pd.Series, benchmark_returns: pd.Series | None, total_return_pct: float) -> dict:
    empty = {"benchmark_return_pct": None, "excess_return_pct": None, "beta": None}
    if benchmark_returns is None or len(benchmark_returns) == 0:
        return empty
    bench = benchmark_returns.reindex(returns.index).fillna(0.0)
    benchmark_return_pct = float((np.prod(1 + bench.to_numpy()) - 1) * 100)
    variance = float(bench.var())
    beta = float(returns.cov(bench) / variance) if len(bench) > 1 and variance > 0 else None
    return {
        "benchmark_return_pct": benchmark_return_pct,
        "excess_return_pct": total_return_pct - benchmark_return_pct,
        "beta": beta,
    }


def consecutive_streaks(profits: pd.Series) -> tuple[int, int]:
    """(longest run of winning trades, longest run of losing trades); a flat trade breaks both."""
    best_wins = best_losses = wins = losses = 0
    for profit in profits.to_numpy(dtype=float):
        if profit > 0:
            wins, losses = wins + 1, 0
        elif profit < 0:
            wins, losses = 0, losses + 1
        else:
            wins = losses = 0
        best_wins, best_losses = max(best_wins, wins), max(best_losses, losses)
    return best_wins, best_losses


def drawdown_stats(equity: pd.Series) -> dict:
    """Deepest peak-to-trough fall: absolute and percent, its peak and trough dates, and the bars
    between them."""
    empty = {
        "max_drawdown_abs": 0.0,
        "max_drawdown_pct": 0.0,
        "drawdown_start": None,
        "drawdown_end": None,
        "drawdown_days": 0,
    }
    if equity.empty:
        return empty
    values = equity.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(values)
    fall = values - peaks
    trough = int(np.argmin(fall))
    depth = -float(fall[trough])
    if depth <= 0:
        return empty
    peak_value = peaks[trough]
    start = int(np.argmax(values[: trough + 1] == peak_value))
    return {
        "max_drawdown_abs": depth,
        "max_drawdown_pct": float(-depth / peak_value * 100) if peak_value else 0.0,
        "drawdown_start": equity.index[start],
        "drawdown_end": equity.index[trough],
        "drawdown_days": trough - start,
    }


def _group_stats(trades: pd.DataFrame, key: str) -> pd.DataFrame:
    grouped = trades.groupby(key, sort=False)
    out = pd.DataFrame(
        {
            "trades": grouped.size(),
            "wins": grouped["profit_abs"].apply(lambda s: int((s > 0).sum())),
            "profit_abs": grouped["profit_abs"].sum(),
            "avg_profit_ratio": grouped["profit_ratio"].mean(),
            "costs": grouped["entry_costs"].sum() + grouped["exit_costs"].sum(),
            "avg_bars_held": grouped["bars_held"].mean(),
        }
    )
    out["win_rate"] = out["wins"] / out["trades"] * 100
    return out.reset_index()


def per_symbol_stats(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per traded symbol, most profitable first."""
    if trades.empty:
        return pd.DataFrame(columns=SYMBOL_STATS_COLUMNS)
    out = _group_stats(trades, "symbol").sort_values("profit_abs", ascending=False, kind="stable")
    return out[SYMBOL_STATS_COLUMNS].reset_index(drop=True)


def per_exit_reason_stats(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per exit reason, most frequent first."""
    if trades.empty:
        return pd.DataFrame(columns=REASON_STATS_COLUMNS)
    out = _group_stats(trades, "exit_reason").sort_values("trades", ascending=False, kind="stable")
    return out[REASON_STATS_COLUMNS].reset_index(drop=True)


def periodic_breakdown(equity: pd.Series, period: str) -> pd.DataFrame:
    """Equity at the end of each day/week/month/year with the return over the period
    (`period` in D/W/M/Y). The first period starts from the first equity value."""
    columns = ["period", "start_equity", "end_equity", "return_pct"]
    if equity.empty:
        return pd.DataFrame(columns=columns)
    ends = equity.resample(PERIOD_RULES[period]).last().dropna()
    starts = ends.shift(1).fillna(float(equity.iloc[0]))
    return pd.DataFrame(
        {
            "period": ends.index,
            "start_equity": starts.to_numpy(dtype=float),
            "end_equity": ends.to_numpy(dtype=float),
            "return_pct": (ends.to_numpy(dtype=float) / starts.to_numpy(dtype=float) - 1) * 100,
        },
        columns=columns,
    )


def annualised_return(returns: pd.Series) -> float:
    """Geometric annual return of a daily series, in percent."""
    if returns.empty:
        return 0.0
    growth = float(np.prod(1 + returns.to_numpy(dtype=float)))
    return (growth ** (TRADING_DAYS / len(returns)) - 1) * 100 if growth > 0 else -100.0
