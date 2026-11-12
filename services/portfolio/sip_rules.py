"""Monthly-SIP simulator: continuous SIP against pause and buy-the-dip rules on one price series.

Every rule receives the same contribution on the same days; a paused contribution waits in cash (earning
`cash_rate_annual`) and is deployed in full on the next month the rule allows buying. Signals use data up to the
previous close only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from core.constants import TRADING_DAYS
from services.constants import RISK_FREE_ANNUAL
from services.portfolio.analytics import compute_xirr

TREND_WINDOW = 200
PE_MIN_HISTORY = 3 * TRADING_DAYS
MONTHS_PER_YEAR = 12


class SipRuleKind(StrEnum):
    CONTINUOUS = "continuous"
    PAUSE_ABOVE_TREND = "pause_above_trend"
    PAUSE_AFTER_RALLY = "pause_after_rally"
    PAUSE_EXPENSIVE = "pause_expensive"
    DIP_RESERVE = "dip_reserve"


@dataclass(frozen=True)
class SipRule:
    """`threshold`: stretch over the 200-day average, trailing 1-year return, or P/E percentile that pauses buying;
    for DIP_RESERVE the share of each instalment held back. `dip`: drawdown from peak that releases the reserve."""

    kind: SipRuleKind
    threshold: float = 0.0
    dip: float = 0.0
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or self.kind.value


@dataclass(frozen=True)
class SipOutcome:
    rule: str
    invested: float
    final_value: float
    cash_left: float
    months_paused: int
    xirr: float | None


@dataclass(frozen=True)
class _Signals:
    stretch: pd.Series
    rally: pd.Series
    drawdown: pd.Series
    pe_pct: pd.Series | None


def _signals(prices: pd.Series, pe: pd.Series | None) -> _Signals:
    prev = prices.shift(1)
    stretch = prev / prices.rolling(TREND_WINDOW).mean().shift(1) - 1
    rally = prev / prices.shift(TRADING_DAYS + 1) - 1
    drawdown = prev / prices.cummax().shift(1) - 1
    pe_pct = None
    if pe is not None:
        aligned = pe.reindex(prices.index).ffill().shift(1)
        pe_pct = aligned.expanding(min_periods=PE_MIN_HISTORY).rank(pct=True)
    return _Signals(stretch, rally, drawdown, pe_pct)


def _blocked(rule: SipRule, sig: _Signals, day: pd.Timestamp) -> bool:
    if rule.kind is SipRuleKind.PAUSE_ABOVE_TREND:
        v = sig.stretch.get(day)
    elif rule.kind is SipRuleKind.PAUSE_AFTER_RALLY:
        v = sig.rally.get(day)
    elif rule.kind is SipRuleKind.PAUSE_EXPENSIVE and sig.pe_pct is not None:
        v = sig.pe_pct.get(day)
    else:
        return False
    return v is not None and pd.notna(v) and v > rule.threshold


def sip_dates(index: pd.DatetimeIndex, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> list:
    """First trading day of each calendar month within [start, end]."""
    idx = index[(index >= (start or index[0])) & (index <= (end or index[-1]))]
    return list(pd.Series(idx, index=idx).groupby(idx.to_period("M")).first())


def simulate_sip(
    prices: pd.Series,
    rule: SipRule,
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    amount: float = 1.0,
    cash_rate_annual: float = RISK_FREE_ANNUAL,
    pe: pd.Series | None = None,
    signals: _Signals | None = None,
) -> SipOutcome:
    """Run one rule over `prices` (date-indexed closes) from `start` to `end`, valued at the last close in range."""
    prices = prices.dropna().sort_index()
    sig = signals or _signals(prices, pe)
    days = sip_dates(prices.index, start, end)
    monthly_cash_growth = (1 + cash_rate_annual) ** (1 / MONTHS_PER_YEAR)
    units = cash = 0.0
    paused = 0
    flows = []
    for day in days:
        cash *= monthly_cash_growth
        flows.append({"date": day, "amount": amount})
        price = float(prices.loc[day])
        if rule.kind is SipRuleKind.DIP_RESERVE:
            cash += amount * rule.threshold
            spend = amount * (1 - rule.threshold)
            dd = sig.drawdown.get(day)
            if dd is not None and pd.notna(dd) and dd <= -rule.dip:
                spend += cash
                cash = 0.0
            units += spend / price
        elif _blocked(rule, sig, day):
            cash += amount
            paused += 1
        else:
            units += (amount + cash) / price
            cash = 0.0
    window = prices.loc[:end] if end is not None else prices
    final_value = units * float(window.iloc[-1]) + cash
    xirr = compute_xirr(pd.DataFrame(flows), final_value, window.index[-1].date()) if flows else None
    return SipOutcome(rule.name, amount * len(days), final_value, cash, paused, xirr)


def compare_rules(
    prices: pd.Series,
    rules: list[SipRule],
    *,
    horizon_months: int,
    step_months: int = 1,
    cash_rate_annual: float = RISK_FREE_ANNUAL,
    pe: pd.Series | None = None,
) -> pd.DataFrame:
    """Every rule over every rolling `horizon_months` window; one row per (window start, rule) with final value and
    its ratio to the CONTINUOUS SIP over the same window."""
    prices = prices.dropna().sort_index()
    sig = _signals(prices, pe)
    starts = sip_dates(prices.index)
    baseline = SipRule(SipRuleKind.CONTINUOUS)
    rows = []
    for i in range(0, len(starts) - horizon_months, step_months):
        start, end = starts[i], starts[i + horizon_months]
        base = simulate_sip(prices, baseline, start=start, end=end, cash_rate_annual=cash_rate_annual, signals=sig)
        for rule in rules:
            out = simulate_sip(prices, rule, start=start, end=end, cash_rate_annual=cash_rate_annual, signals=sig)
            rows.append(
                {
                    "start": start,
                    "rule": out.rule,
                    "final_value": out.final_value,
                    "vs_continuous": out.final_value / base.final_value - 1,
                    "months_paused": out.months_paused,
                    "xirr": out.xirr,
                    "continuous_xirr": base.xirr,
                }
            )
    return pd.DataFrame(rows)


def summarize_rule_comparison(windows: pd.DataFrame) -> pd.DataFrame:
    """Per rule across windows: share of windows beating continuous SIP, median / worst / best edge, avg months paused."""
    return (
        windows.groupby("rule")
        .agg(
            windows=("vs_continuous", "size"),
            beat_rate=("vs_continuous", lambda s: (s > 0).mean()),
            median_edge=("vs_continuous", "median"),
            worst_edge=("vs_continuous", "min"),
            best_edge=("vs_continuous", "max"),
            avg_months_paused=("months_paused", "mean"),
        )
        .sort_values("median_edge", ascending=False)
    )
