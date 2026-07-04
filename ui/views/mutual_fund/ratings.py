"""Interpretive good/bad ratings for MF Analysis KPI pills.

Each `rate_*` returns a (streamlit-color, short-label) badge that makes a metric self-explanatory
— e.g. a Sharpe of 1.3 reads "Good", a 28% drawdown reads "Deep". Thresholds are tuned for
**equity** funds (the common case); debt/liquid funds run lower on every axis, so their bands
read conservatively — a caveat, not a bug.

Green = healthy, orange = caution / middling, red = poor, blue = neutral/informational.
All inputs are decimal fractions (0.15 = 15%) except TER (a percent number, e.g. 0.61) and AUM (₹Cr).
"""

from __future__ import annotations

Pill = tuple[str, str]


def _ok(v: object) -> bool:
    return isinstance(v, (int, float)) and v == v  # not None, not NaN


def rate_return_vs_benchmark(alpha_1y: float | None) -> Pill | None:
    """1Y return quality = did it beat its benchmark? Uses annualised Jensen alpha, so it's
    risk-adjusted (a high return in a raging market isn't automatically 'good')."""
    if not _ok(alpha_1y):
        return None
    if alpha_1y > 0.02:
        return ("green", "Beating benchmark")
    if alpha_1y < -0.02:
        return ("red", "Lagging benchmark")
    return ("orange", "In line w/ benchmark")


def rate_cagr(cagr: float | None) -> Pill | None:
    """Long-run wealth compounding. Equity: ~12%+ is strong, single digits is modest."""
    if not _ok(cagr):
        return None
    if cagr >= 0.12:
        return ("green", "Strong")
    if cagr >= 0.08:
        return ("blue", "Healthy")
    if cagr >= 0:
        return ("orange", "Modest")
    return ("red", "Negative")


def rate_sharpe(sharpe: float | None) -> Pill | None:
    """Return per unit of total risk. >1 rewards you well for the volatility; <0.5 barely does."""
    if not _ok(sharpe):
        return None
    if sharpe >= 1.0:
        return ("green", "Good risk-adj.")
    if sharpe >= 0.5:
        return ("orange", "Fair risk-adj.")
    return ("red", "Weak risk-adj.")


def rate_drawdown(max_dd: float | None) -> Pill | None:
    """Worst peak-to-trough fall — how much pain to stomach. Magnitude drives it."""
    if not _ok(max_dd):
        return None
    depth = abs(max_dd)
    if depth < 0.10:
        return ("green", "Shallow")
    if depth <= 0.20:
        return ("orange", "Moderate")
    return ("red", "Deep")


def rate_volatility(vol: float | None) -> Pill | None:
    """Annualised standard deviation — how bumpy the ride is."""
    if not _ok(vol):
        return None
    if vol < 0.12:
        return ("green", "Low")
    if vol <= 0.20:
        return ("orange", "Moderate")
    return ("red", "High")


def rate_ter(ter_pct: float | None) -> Pill | None:
    """Expense ratio — the drag on returns. Direct plans/index funds are cheap; active regular dear."""
    if not _ok(ter_pct):
        return None
    if ter_pct < 0.5:
        return ("green", "Low cost")
    if ter_pct <= 1.0:
        return ("orange", "Moderate cost")
    return ("red", "Expensive")


def rate_aum(aum_cr: float | None) -> Pill | None:
    """Fund size — informational. Very small = thin track record/liquidity; huge can hamper
    small/mid-cap agility. Neutral by default, a nudge on the extremes."""
    if not _ok(aum_cr):
        return None
    if aum_cr >= 5000:
        return ("blue", "Large")
    if aum_cr >= 1000:
        return ("blue", "Mid-sized")
    if aum_cr >= 300:
        return ("blue", "Small")
    return ("orange", "Very small")
