"""The new-run composer and the edit dialog.

Both draw the same controls — strategy, its declared parameters, risk and sizing overrides, the
universe, the cost model and the period — and both end in `build_config`, the one place a
`BacktestConfig` is assembled from widget values. A finished run is immutable, so editing one saves
a new draft instead of rewriting the result.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

import streamlit as st

from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import PRESET_LABELS, PRESETS
from ui.components.chrome import section_header
from ui.state.loaders import clear_backtest_caches, load_backtest_run_cached
from ui.views.backtest_lab import params as param_widgets
from ui.views.backtest_lab import state, universe_picker

if TYPE_CHECKING:
    from collections.abc import Callable

    from services.backtest.runs import StrategySpec

logger = logging.getLogger(__name__)

FORM_PREFIX = "bt_form_"
EDIT_PREFIX = "bt_edit_"
DEFAULT_YEARS = 5
DEFAULT_SLIPPAGE_BPS = 5.0
DEFAULT_CASH = 1_000_000.0


def default_start(today: datetime.date | None = None) -> datetime.date:
    today = today or datetime.date.today()
    return today.replace(year=today.year - DEFAULT_YEARS)


def default_name(strategy: str, universe: UniverseSpec) -> str:
    return f"{strategy} · {universe.label()}"


def _as_date(value: Any, fallback: datetime.date) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def build_config(
    *,
    strategy: str,
    params: dict,
    universe: UniverseSpec,
    symbols: list[str],
    start: datetime.date,
    end: datetime.date,
    init_cash: float,
    preset: str,
    slippage_bps: float,
    overrides: dict,
    stake_amount: float | None,
) -> BacktestConfig:
    """Widget values → the config a run is stored as. Watchlist universes are frozen to the symbols
    they resolve to right now, so a later watchlist edit cannot change what a saved run ran on."""
    return BacktestConfig(
        strategy=strategy,
        params=dict(params),
        universe=universe_picker.freeze(universe, symbols),
        start=start,
        end=end,
        init_cash=float(init_cash),
        costs=PRESETS[preset],
        slippage_bps=float(slippage_bps),
        strategy_overrides=dict(overrides),
        stake_amount=stake_amount,
    )


def _strategy_caption(spec: StrategySpec) -> str:
    mode = "cross-sectional top-K rebalancing" if spec.mode == "rank" else "per-stock signals"
    ml = " · walk-forward ML (slow, run one at a time)" if spec.is_ml else ""
    return f"{mode} · {spec.startup_candle_count} warm-up bars{ml}"


def _compose(
    catalog: dict[str, StrategySpec], *, prefix: str, values: dict | None = None, force: bool = False
) -> tuple[BacktestConfig | None, str]:
    """Draw every control and return the composed config with the run name (None when unusable)."""
    values = values or {}
    names = sorted(catalog)
    strategy_key, cash_key = f"{prefix}strategy", f"{prefix}cash"
    cost_key, slip_key = f"{prefix}costs", f"{prefix}slippage"
    start_key, end_key, name_key = f"{prefix}start", f"{prefix}end", f"{prefix}name"

    stored = values.get("strategy")
    param_widgets.seed(strategy_key, stored if stored in names else names[0], force=force)
    strategy = st.selectbox("Strategy", names, key=strategy_key)
    spec = catalog[strategy]
    st.caption(_strategy_caption(spec))

    section_header("Parameters", "Declared by the strategy; the optimiser searches the same set.")
    chosen = param_widgets.render_parameter_widgets(spec, values.get("params"), prefix=prefix, force=force)

    section_header("Risk and sizing", "Applied on top of the strategy's own defaults.")
    overrides, stake = param_widgets.render_override_widgets(
        spec, values.get("strategy_overrides"), prefix=prefix, stake=values.get("stake_amount"), force=force
    )

    section_header("Universe", "Every symbol is loaded DB-first; only missing bars are fetched.")
    stored_universe = UniverseSpec.from_dict(values["universe"]) if values.get("universe") else None
    universe, symbols = universe_picker.render(prefix=prefix, universe=stored_universe, force=force)

    section_header("Costs and period", "Zerodha charges per order plus slippage on every fill.")
    left, middle, right = st.columns(3)
    with left:
        preset_names = list(PRESETS)
        stored_preset = (values.get("costs") or {}).get("name")
        param_widgets.seed(cost_key, stored_preset if stored_preset in preset_names else preset_names[0], force=force)
        preset = st.selectbox("Cost preset", preset_names, format_func=lambda p: PRESET_LABELS[p], key=cost_key)
        param_widgets.seed(slip_key, float(values.get("slippage_bps", DEFAULT_SLIPPAGE_BPS)), force=force)
        slippage = float(st.number_input("Slippage (bps)", min_value=0.0, max_value=200.0, step=1.0, key=slip_key))
    with middle:
        param_widgets.seed(start_key, _as_date(values.get("start"), default_start()), force=force)
        start = st.date_input("Start", key=start_key)
        param_widgets.seed(end_key, _as_date(values.get("end"), datetime.date.today()), force=force)
        end = st.date_input("End", key=end_key)
    with right:
        param_widgets.seed(cash_key, float(values.get("init_cash", DEFAULT_CASH)), force=force)
        init_cash = float(st.number_input("Starting cash (₹)", min_value=10_000.0, step=50_000.0, key=cash_key))
        param_widgets.seed(name_key, values.get("name") or default_name(strategy, universe), force=force)
        name = st.text_input("Run name", key=name_key)

    if preset == "zerodha_intraday":
        st.caption("The engine holds positions overnight — intraday pricing is for cost comparison only.")
    if not symbols or start >= end:
        if start >= end:
            st.warning("The start date must fall before the end date.")
        return None, name
    config = build_config(
        strategy=strategy,
        params=chosen,
        universe=universe,
        symbols=symbols,
        start=start,
        end=end,
        init_cash=init_cash,
        preset=preset,
        slippage_bps=slippage,
        overrides=overrides,
        stake_amount=stake,
    )
    return config, (name or default_name(strategy, universe))


def _create(config: BacktestConfig, name: str) -> int:
    from services.backtest.runs import create_run  # noqa: PLC0415 — pulls the engine + quantstats in

    run_id = create_run(config, name)
    clear_backtest_caches()
    logger.info("backtest lab: created run %s (%s)", run_id, config.strategy)
    return run_id


def render_new_run(catalog: dict[str, StrategySpec], *, launch: Callable[[list[int]], list[int]]) -> None:
    """The composer inside the Runs view's expander. `launch` queues the run when asked to."""
    prefill = state.take_prefill()
    config, name = _compose(catalog, prefix=FORM_PREFIX, values=prefill, force=prefill is not None)
    create_col, run_col, _ = st.columns([1, 1, 4])
    disabled = config is None
    create_clicked = create_col.button("Create", key="bt_form_create", disabled=disabled, use_container_width=True)
    run_clicked = run_col.button(
        "Create and run", key="bt_form_create_run", type="primary", disabled=disabled, use_container_width=True
    )
    if config is None or not (create_clicked or run_clicked):
        return
    run_id = _create(config, name)
    state.set_selected_run(run_id)
    if run_clicked:
        launch([run_id])
    st.toast(f"Created “{name}”.", icon="✅")
    st.rerun()


@st.dialog("Edit run", width="large")
def edit_dialog(run_id: int, catalog: dict[str, StrategySpec]) -> None:
    """Edit a run's configuration. Drafts are rewritten in place; a finished run's edits are saved as
    a new draft, because its results are immutable."""
    detail = load_backtest_run_cached(run_id)
    if detail is None:
        st.warning("That run no longer exists.")
        return
    loaded_key = f"{EDIT_PREFIX}loaded"
    fresh = st.session_state.get(loaded_key) != run_id
    st.session_state[loaded_key] = run_id
    values = {**detail.config.to_dict(), "name": detail.name}
    config, name = _compose(catalog, prefix=EDIT_PREFIX, values=values, force=fresh)
    is_draft = detail.status == "draft"
    st.caption("Saving rewrites this draft." if is_draft else "This run has results — saving creates a new draft.")
    if st.button("Save", key="bt_edit_save", type="primary", disabled=config is None, use_container_width=True):
        from services.backtest.runs import update_run  # noqa: PLC0415 — pulls the engine + quantstats in

        if is_draft:
            update_run(run_id, config, name)
            clear_backtest_caches()
            new_id = run_id
        else:
            new_id = _create(config, f"{name} (edited)")
        state.set_selected_run(new_id)
        st.session_state.pop(loaded_key, None)
        st.toast("Saved.", icon="✅")
        st.rerun()
