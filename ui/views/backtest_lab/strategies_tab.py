"""Strategies view: what the app found, where it found it, and how to add one.

The picker on the Runs view only shows names. This view shows the whole catalogue the way
`freqtrade list-strategies` does — built-ins alongside anything dropped into the strategy directory,
with the file each one came from, the parameters it declares and the risk defaults it ships. A file
that failed to import is reported here rather than swallowed, because otherwise a typo just makes a
strategy quietly disappear from the picker.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from ui.components.chrome import section_header
from ui.state.loaders import load_strategy_health_cached, reload_strategies
from ui.views.backtest_lab import state

if TYPE_CHECKING:
    from services.backtest.runs import StrategySpec

logger = logging.getLogger(__name__)

PICKER_KEY = "bt_strategy_detail"
NEW_NAME_KEY = "bt_new_strategy_name"
NEW_LABEL_KEY = "bt_new_strategy_label"
NEW_MODE_KEY = "bt_new_strategy_mode"
ORIGIN_LABELS = {"builtin": "Built-in", "user": "Your file"}
MODE_HELP = {
    "signal": "Per-symbol entry and exit signals, capped at `max_open_trades` open positions.",
    "rank": "A cross-sectional score; the top `top_k` are held and rebalanced with hysteresis.",
}


def render(catalog: dict[str, StrategySpec]) -> None:
    health = load_strategy_health_cached()
    _render_toolbar(health)
    _render_problems(health)
    if not catalog:
        st.info("No strategies were found. Check the strategy directory above.")
        return
    _render_catalogue(catalog)
    _render_detail(catalog)
    _render_new_strategy(health)


def _render_toolbar(health: dict) -> None:
    left, right = st.columns([4, 1], vertical_alignment="bottom")
    with left:
        st.caption(f"Strategy directory: `{health.get('directory', '')}`")
    with right:
        if st.button("Reload from disk", use_container_width=True, icon=":material/refresh:"):
            reload_strategies()
            st.rerun(scope="app")


def _render_problems(health: dict) -> None:
    for message in health.get("warnings") or []:
        st.warning(message, icon=":material/warning:")
    errors = health.get("errors") or []
    if not errors:
        return
    st.error(f"{len(errors)} strategy file(s) could not be imported and are not selectable.")
    for source, trace in errors:
        with st.expander(source, icon=":material/error:"):
            st.code(trace, language="text")


def _catalogue_frame(catalog: dict[str, StrategySpec]) -> pd.DataFrame:
    rows = [
        {
            "Strategy": spec.name,
            "Mode": spec.mode,
            "Source": ORIGIN_LABELS.get(spec.origin, spec.origin),
            "Parameters": len(spec.parameters),
            "Tunable": sum(1 for p in spec.parameters.values() if p.optimize),
            "Stop": spec.stoploss,
            "Slots": spec.top_k if spec.mode == "rank" else spec.max_open_trades,
            "Warm-up": spec.startup_candle_count,
            "File": spec.source,
        }
        for spec in catalog.values()
    ]
    return pd.DataFrame(rows).sort_values(["Source", "Strategy"], ascending=[False, True])


def _render_catalogue(catalog: dict[str, StrategySpec]) -> None:
    section_header("Available strategies", "Everything the run picker can select.")
    st.dataframe(
        _catalogue_frame(catalog),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Stop": st.column_config.NumberColumn("Stop", format="percent"),
            "Warm-up": st.column_config.NumberColumn("Warm-up", format="%d bars"),
            "File": st.column_config.TextColumn("File", width="large"),
        },
    )


def _parameter_frame(spec: StrategySpec) -> pd.DataFrame:
    rows = [
        {
            "Parameter": name,
            "Default": str(param.default),
            "Range": _range_text(param),
            "Tuned": param.optimize,
            "What it does": param.help or "",
        }
        for name, param in spec.parameters.items()
    ]
    return pd.DataFrame(rows)


def _range_text(param) -> str:
    if param.kind in ("categorical", "bool"):
        return ", ".join(str(c) for c in param.choices)
    step = f" step {param.step:g}" if getattr(param, "step", None) else ""
    return f"{param.low:g} to {param.high:g}{step}"


def _render_detail(catalog: dict[str, StrategySpec]) -> None:
    names = list(catalog)
    if st.session_state.get(PICKER_KEY) not in names:
        st.session_state[PICKER_KEY] = names[0]
    name = st.selectbox("Strategy", names, key=PICKER_KEY)
    spec = catalog[name]

    header, action = st.columns([4, 1], vertical_alignment="bottom")
    with header:
        section_header(spec.name, MODE_HELP.get(spec.mode, ""))
    with action:
        if st.button("Backtest this", use_container_width=True, icon=":material/play_arrow:"):
            st.session_state[state.PREFILL_PENDING] = {"strategy": spec.name}
            state.go_to("Runs")

    if spec.doc:
        st.markdown(spec.doc)
    st.caption(f"{ORIGIN_LABELS.get(spec.origin, spec.origin)} · `{spec.source}`")

    _render_defaults(spec)
    parameters = _parameter_frame(spec)
    if parameters.empty:
        st.caption("This strategy declares no tunable parameters.")
        return
    st.dataframe(
        parameters,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Tuned": st.column_config.CheckboxColumn("Optimiser", help="Whether the optimiser may search this"),
            "What it does": st.column_config.TextColumn("What it does", width="large"),
        },
    )


def _render_defaults(spec: StrategySpec) -> None:
    roi = ", ".join(f"{bars}→{value:.0%}" for bars, value in sorted(spec.minimal_roi.items())) or "off"
    shared = [
        ("Stop", "off" if spec.stoploss <= -0.9 else f"{spec.stoploss:.0%}"),
        ("Trailing", "on" if spec.trailing_stop else "off"),
        ("ROI ladder", roi),
        ("Warm-up", f"{spec.startup_candle_count} bars"),
    ]
    basket = (
        [
            ("Hold", f"top {spec.top_k}"),
            ("Exit band", str(spec.exit_rank or spec.top_k)),
            ("Rebalance", str(spec.rebalance_every)),
        ]
        if spec.mode == "rank"
        else [("Open positions", str(spec.max_open_trades))]
    )
    fields = shared + basket
    for column, (label, value) in zip(st.columns(len(fields)), fields, strict=True):
        column.metric(label, value)


def _render_new_strategy(health: dict) -> None:
    with st.expander("New strategy from template", icon=":material/note_add:"):
        st.caption(
            f"Writes a starter file into `{health.get('directory', '')}`. Edit it there and it "
            "appears in the picker on the next reload."
        )
        with st.form("bt_new_strategy_form"):
            left, middle, right = st.columns([2, 2, 1])
            class_name = left.text_input("Class name", key=NEW_NAME_KEY, placeholder="MyIdea")
            label = middle.text_input("Display name", key=NEW_LABEL_KEY, placeholder="My Idea")
            mode = right.selectbox("Mode", ("signal", "rank"), key=NEW_MODE_KEY)
            if st.form_submit_button("Create", icon=":material/add:"):
                _create(class_name, label, mode)


def _create(class_name: str, label: str, mode: str) -> None:
    from services.backtest.runs import create_strategy_file  # noqa: PLC0415 — pulls the engine in

    name = (class_name or "").strip()
    if not name:
        st.error("Give the class a name.")
        return
    try:
        path = create_strategy_file(name, display_name=(label or "").strip() or None, mode=mode)
    except (ValueError, FileExistsError, OSError) as exc:
        st.error(str(exc))
        return
    reload_strategies()
    st.success(f"Created `{path}`. Edit it, then reload.")
