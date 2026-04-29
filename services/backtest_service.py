"""Backtest service — runs strategies and computes performance metrics."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import quantstats as qs
import vectorbt as vbt

RISK_FREE = 0.065  # Indian risk-free rate


@dataclass
class BacktestResult:
    portfolio: object  # vbt.Portfolio
    indicators: dict
    entries: pd.Series
    exits: pd.Series
    returns: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def run_backtest(
    price: pd.Series,
    strategy_cls: type,
    params: dict,
    init_cash: float = 100_000,
    fees: float = 0.001,
    use_sl: bool = False,
    sl_pct: float = 0.0,
    use_trail: bool = False,
) -> BacktestResult:
    """Execute a strategy backtest and return results."""
    strategy = strategy_cls(**params)
    indicators = strategy.indicators(price)
    entries, exits = strategy.signals(price, indicators)

    pf_kwargs = dict(
        close=price,
        entries=entries,
        exits=exits,
        init_cash=init_cash,
        fees=fees,
        slippage=0.001,
        freq="1D",
    )
    if use_sl and sl_pct > 0:
        pf_kwargs["sl_stop"] = sl_pct
        if use_trail:
            pf_kwargs["sl_trail"] = True

    portfolio = vbt.Portfolio.from_signals(**pf_kwargs)
    returns = portfolio.returns()
    trades = portfolio.trades.records_readable

    result = BacktestResult(
        portfolio=portfolio,
        indicators=indicators,
        entries=entries,
        exits=exits,
        returns=returns,
        trades=trades,
    )

    if not returns.empty and len(returns) >= 2 and portfolio.trades.count() > 0:
        result.metrics = compute_metrics(returns, trades)

    return result


def compute_metrics(returns: pd.Series, trades: pd.DataFrame) -> dict:
    """Compute comprehensive backtest metrics from returns and trades."""
    winning = trades[trades["Return"] > 0]
    losing = trades[trades["Return"] < 0]
    n_trades = len(trades)

    trade_wr = len(winning) / n_trades * 100 if n_trades > 0 else 0
    avg_win = winning["Return"].mean() * 100 if len(winning) > 0 else 0
    avg_loss = losing["Return"].mean() * 100 if len(losing) > 0 else 0
    best_trade = trades["Return"].max() * 100 if n_trades > 0 else 0
    worst_trade = trades["Return"].min() * 100 if n_trades > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    gross_wins = winning["PnL"].sum() if len(winning) > 0 else 0
    gross_losses = abs(losing["PnL"].sum()) if len(losing) > 0 else 0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    expectancy = trades["PnL"].mean() if n_trades > 0 else 0

    if n_trades > 1 and trades["Return"].std() > 0:
        sqn = (trades["Return"].mean() / trades["Return"].std()) * np.sqrt(n_trades)
    else:
        sqn = 0

    avg_duration = "N/A"
    if n_trades > 0 and "Entry Timestamp" in trades.columns and "Exit Timestamp" in trades.columns:
        durations = pd.to_datetime(trades["Exit Timestamp"]) - pd.to_datetime(trades["Entry Timestamp"])
        avg_duration = str(durations.mean()).split(".")[0] if len(durations) > 0 else "N/A"

    return {
        "cagr": qs.stats.cagr(returns) * 100,
        "cumulative_return": qs.stats.comp(returns) * 100,
        "max_drawdown": qs.stats.max_drawdown(returns) * 100,
        "annual_volatility": qs.stats.volatility(returns) * 100,
        "sharpe": qs.stats.sharpe(returns, rf=RISK_FREE / 252),
        "sortino": qs.stats.sortino(returns, rf=RISK_FREE / 252),
        "calmar": qs.stats.calmar(returns),
        "profit_factor": profit_factor,
        "win_rate": trade_wr,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "expectancy": expectancy,
        "sqn": sqn,
        "var_95": qs.stats.var(returns) * 100,
        "cvar_95": qs.stats.cvar(returns) * 100,
        "kelly": qs.stats.kelly_criterion(returns) * 100,
        "avg_duration": avg_duration,
    }
