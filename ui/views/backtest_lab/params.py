"""Widgets generated from a strategy's declared parameters, plus the risk and sizing attributes a
run may override on top of the strategy class.

Every widget is keyed `bt_<form>_p_<strategy_slug>_<parameter>` so two strategies never share a key
and the values survive a rerun. Values are seeded into session state before the widget renders, the
way `ui/state/filter_persistence.hydrate_filters` does, so no widget carries both a `value=` and a
session-state entry.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import streamlit as st

if TYPE_CHECKING:
    from services.backtest.runs import StrategySpec

PARAM_COLUMNS = 3
REBALANCE_CHOICES = ("W", "M", "5", "10", "21")
REBALANCE_LABELS = {"W": "Weekly", "M": "Monthly", "5": "Every 5 bars", "10": "Every 10 bars", "21": "Every 21 bars"}
_SUMMARY_LIMIT = 4


def strategy_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def param_key(prefix: str, strategy: str, name: str) -> str:
    return f"{prefix}p_{strategy_slug(strategy)}_{name}"


def seed(key: str, value: Any, *, force: bool = False) -> None:
    if force or key not in st.session_state:
        st.session_state[key] = value


def _seed_number(key: str, value: float, low: float, high: float, *, force: bool) -> None:
    seed(key, min(max(value, low), high), force=force)


def _param_widget(param: Any, key: str, label: str) -> Any:
    help_text = param.help or None
    if param.kind == "bool":
        return bool(st.checkbox(label, key=key, help=help_text))
    if param.kind == "categorical":
        return st.selectbox(label, list(param.choices), key=key, help=help_text)
    if param.kind == "int":
        return int(
            st.number_input(
                label,
                min_value=int(param.low),
                max_value=int(param.high),
                step=int(param.step) or 1,
                key=key,
                help=help_text,
            )
        )
    decimals = getattr(param, "decimals", 2)
    return float(
        st.number_input(
            label,
            min_value=float(param.low),
            max_value=float(param.high),
            step=float(param.step),
            format=f"%.{decimals}f",
            key=key,
            help=help_text,
        )
    )


def render_parameter_widgets(
    spec: StrategySpec, values: dict | None = None, *, prefix: str, force: bool = False
) -> dict[str, Any]:
    """One widget per declared parameter, laid out three to a row. Returns the chosen values."""
    values = values or {}
    if not spec.parameters:
        st.caption("This strategy declares no parameters.")
        return {}
    for name, param in spec.parameters.items():
        key = param_key(prefix, spec.name, name)
        value = values.get(name, param.default)
        if param.kind in ("bool", "categorical"):
            seed(key, value if (param.kind == "bool" or value in param.choices) else param.default, force=force)
        else:
            _seed_number(key, value, param.low, param.high, force=force)
    chosen: dict[str, Any] = {}
    items = list(spec.parameters.items())
    for start in range(0, len(items), PARAM_COLUMNS):
        for column, (name, param) in zip(st.columns(PARAM_COLUMNS), items[start : start + PARAM_COLUMNS], strict=False):
            with column:
                chosen[name] = _param_widget(param, param_key(prefix, spec.name, name), name.replace("_", " "))
    return chosen


def _rebalance_value(choice: str) -> int | str:
    return int(choice) if choice.isdigit() else choice


def render_override_widgets(
    spec: StrategySpec, overrides: dict | None = None, *, prefix: str, stake: float | None = None, force: bool = False
) -> tuple[dict[str, Any], float | None]:
    """Risk and sizing attributes layered over the strategy class. Only values that actually differ
    from the class defaults are returned, so a stored run records the overrides it really applied."""
    overrides = overrides or {}
    keys = {name: f"{prefix}{name}" for name in ("stoploss", "trailing_stop", "max_open_trades", "stake_amount")}
    _seed_number(keys["stoploss"], float(overrides.get("stoploss", spec.stoploss)), -0.9, -0.005, force=force)
    seed(keys["trailing_stop"], bool(overrides.get("trailing_stop", spec.trailing_stop)), force=force)
    _seed_number(
        keys["max_open_trades"], int(overrides.get("max_open_trades", spec.max_open_trades)), 1, 50, force=force
    )
    _seed_number(keys["stake_amount"], float(stake or 0.0), 0.0, 1e9, force=force)

    left, middle, right, far = st.columns(4)
    with left:
        stoploss = float(
            st.number_input(
                "Stoploss",
                min_value=-0.9,
                max_value=-0.005,
                step=0.005,
                format="%.3f",
                key=keys["stoploss"],
                help="Fraction below the entry price at which a position is closed intrabar.",
            )
        )
    with middle:
        trailing = bool(st.checkbox("Trailing stop", key=keys["trailing_stop"], help="Ratchet the stop up with price."))
    with right:
        max_open = int(
            st.number_input("Max open trades", min_value=1, max_value=50, step=1, key=keys["max_open_trades"])
        )
    with far:
        stake_amount = float(
            st.number_input(
                "Stake per trade (₹)",
                min_value=0.0,
                step=10_000.0,
                format="%.0f",
                key=keys["stake_amount"],
                help="0 splits free cash across the free slots.",
            )
        )

    chosen: dict[str, Any] = {}
    if stoploss != spec.stoploss:
        chosen["stoploss"] = stoploss
    if trailing != spec.trailing_stop:
        chosen["trailing_stop"] = trailing
    if max_open != spec.max_open_trades:
        chosen["max_open_trades"] = max_open
    if spec.mode == "rank":
        chosen.update(_rank_overrides(spec, overrides, prefix=prefix, force=force))
    return chosen, (stake_amount or None)


def _rank_overrides(spec: StrategySpec, overrides: dict, *, prefix: str, force: bool) -> dict[str, Any]:
    """Basket sizing for rank-mode strategies: how many names to hold, when to rotate, when to drop."""
    top_key, exit_key, cadence_key = f"{prefix}top_k", f"{prefix}exit_rank", f"{prefix}rebalance_every"
    _seed_number(top_key, int(overrides.get("top_k", spec.top_k)), 1, 100, force=force)
    _seed_number(exit_key, int(overrides.get("exit_rank") or spec.exit_rank or 0), 0, 200, force=force)
    cadence = str(overrides.get("rebalance_every", spec.rebalance_every))
    seed(cadence_key, cadence if cadence in REBALANCE_CHOICES else "M", force=force)

    left, middle, right = st.columns(3)
    with left:
        top_k = int(st.number_input("Top K", min_value=1, max_value=100, step=1, key=top_key))
    with middle:
        exit_rank = int(
            st.number_input(
                "Exit rank",
                min_value=0,
                max_value=200,
                step=1,
                key=exit_key,
                help="0 drops a name as soon as it "
                "leaves the top K; a higher value holds it until its rank slips past this.",
            )
        )
    with right:
        cadence = st.selectbox(
            "Rebalance", REBALANCE_CHOICES, format_func=lambda c: REBALANCE_LABELS[c], key=cadence_key
        )
    chosen: dict[str, Any] = {}
    if top_k != spec.top_k:
        chosen["top_k"] = top_k
    if (exit_rank or None) != spec.exit_rank:
        chosen["exit_rank"] = exit_rank or None
    if _rebalance_value(cadence) != spec.rebalance_every:
        chosen["rebalance_every"] = _rebalance_value(cadence)
    return chosen


def _fmt(value: Any) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def params_summary(params: dict | None, limit: int = _SUMMARY_LIMIT) -> str:
    """`window=14 · low=30 · +2 more` — the grid's one-cell view of a run's parameters."""
    if not params:
        return "—"
    items = [f"{k}={_fmt(v)}" for k, v in list(params.items())[:limit]]
    extra = len(params) - len(items)
    return " · ".join(items) + (f" · +{extra} more" if extra > 0 else "")
