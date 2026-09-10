"""What the optimiser maximises.

Ratios come from the daily equity curve, not from closed trades: a hold-and-rebalance book closes
few trades, and trade-based Sharpe or Calmar on such a book reports a number that has little to do
with the money at risk. `score` returns None when a run has too few trades to judge, which the
optimiser records as a pruned trial rather than a bad one.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from core.constants import TRADING_DAYS
from services.constants import BACKTEST_RISK_FREE

if TYPE_CHECKING:
    from services.backtest.engine import BacktestResult

Objective = Literal["sharpe", "sortino", "calmar", "profit_factor", "expectancy", "total_return_dd"]

OBJECTIVE_LABELS = {
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "calmar": "Calmar",
    "profit_factor": "Profit factor",
    "expectancy": "Expectancy",
    "total_return_dd": "Return minus drawdown",
}
DEFAULT_MIN_TRADES = 20
RF_DAILY = BACKTEST_RISK_FREE / TRADING_DAYS


def daily_returns(equity: pd.Series) -> pd.Series:
    if equity is None or len(equity) < 2:
        return pd.Series(dtype=float)
    return equity.pct_change().dropna()


def sharpe(result: BacktestResult) -> float:
    returns = daily_returns(result.equity)
    excess = returns - RF_DAILY
    sd = float(excess.std())
    if not returns.size or sd <= 0:
        return 0.0
    return float(excess.mean() / sd * math.sqrt(TRADING_DAYS))


def sortino(result: BacktestResult) -> float:
    returns = daily_returns(result.equity)
    excess = returns - RF_DAILY
    downside = excess[excess < 0]
    sd = float(downside.std())
    if not returns.size or sd <= 0:
        return 0.0
    return float(excess.mean() / sd * math.sqrt(TRADING_DAYS))


def max_drawdown_pct(equity: pd.Series) -> float:
    """Deepest peak-to-trough fall as a positive percentage."""
    if equity is None or equity.empty:
        return 0.0
    values = equity.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(values)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = np.where(peaks > 0, values / peaks - 1, 0.0)
    return float(-drawdowns.min() * 100)


def total_return_pct(equity: pd.Series) -> float:
    if equity is None or len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0] - 1) * 100)


def calmar(result: BacktestResult) -> float:
    returns = daily_returns(result.equity)
    drawdown = max_drawdown_pct(result.equity)
    if not returns.size or drawdown <= 0:
        return 0.0
    growth = float(result.equity.iloc[-1] / result.equity.iloc[0])
    if growth <= 0:
        return 0.0
    annual = (growth ** (TRADING_DAYS / len(returns)) - 1) * 100
    return float(annual / drawdown)


def profit_factor(result: BacktestResult) -> float:
    trades = result.trades
    if trades.empty:
        return 0.0
    profits = trades["profit_abs"]
    gains = float(profits[profits > 0].sum())
    losses = float(-profits[profits < 0].sum())
    if losses <= 0:
        return gains if gains > 0 else 0.0
    return gains / losses


def expectancy(result: BacktestResult) -> float:
    """Average rupees per trade."""
    trades = result.trades
    return 0.0 if trades.empty else float(trades["profit_abs"].mean())


def total_return_dd(result: BacktestResult, penalty: float = 1.0) -> float:
    return total_return_pct(result.equity) - penalty * max_drawdown_pct(result.equity)


OBJECTIVES = {
    "sharpe": sharpe,
    "sortino": sortino,
    "calmar": calmar,
    "profit_factor": profit_factor,
    "expectancy": expectancy,
    "total_return_dd": total_return_dd,
}


def score(result: BacktestResult, objective: str, *, min_trades: int = DEFAULT_MIN_TRADES) -> float | None:
    """The objective value, or None when the run has too few trades or the value is not finite."""
    if objective not in OBJECTIVES:
        raise KeyError(f"unknown objective {objective!r}")
    if len(result.trades) < min_trades:
        return None
    value = OBJECTIVES[objective](result)
    return None if value is None or not math.isfinite(value) else float(value)
