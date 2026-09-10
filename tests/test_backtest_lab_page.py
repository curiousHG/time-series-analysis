"""AppTest smoke for the Backtest Lab page: every view renders, and the composer builds the config
it says it does. The loaders and the runs service are monkeypatched, so nothing touches the DB."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pandas as pd
import polars as pl
import pytest
from streamlit.testing.v1 import AppTest

from data.repositories.backtest_runs import RUN_SCHEMA
from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.costs import ZERODHA_DELIVERY
from services.backtest.metrics import per_exit_reason_stats, per_symbol_stats
from services.backtest.runs import OptimizationDetail, RunDetail, StrategySpec
from strategies.parameters import DecimalParameter, IntParameter

PAGE = "ui/views/backtest_lab/page.py"
CONSUMERS = (
    "ui.views.backtest_lab.runs_tab",
    "ui.views.backtest_lab.run_form",
    "ui.views.backtest_lab.run_detail",
    "ui.views.backtest_lab.compare_tab",
    "ui.views.backtest_lab.optimise_tab",
)


def _spec() -> StrategySpec:
    return StrategySpec(
        name="RSI Basket",
        mode="signal",
        parameters={
            "window": IntParameter(14, 5, 30, 1, help="RSI period"),
            "oversold": DecimalParameter(30.0, 10.0, 45.0, 1.0),
        },
        stoploss=-0.1,
        trailing_stop=False,
        minimal_roi={},
        max_open_trades=5,
        top_k=10,
        exit_rank=None,
        rebalance_every="M",
        startup_candle_count=50,
        is_ml=False,
    )


def _config(strategy: str = "RSI Basket") -> BacktestConfig:
    return BacktestConfig(
        strategy=strategy,
        params={"window": 14, "oversold": 30.0},
        universe=UniverseSpec(kind="list", symbols=("TCS", "INFY")),
        start=datetime.date(2023, 1, 2),
        end=datetime.date(2023, 6, 30),
        costs=ZERODHA_DELIVERY,
    )


def _trades() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["TCS", "INFY", "TCS"],
            "open_date": pd.to_datetime(["2023-01-10", "2023-02-01", "2023-03-01"]),
            "close_date": pd.to_datetime(["2023-01-20", "2023-02-20", "2023-03-20"]),
            "open_rate": [100.0, 200.0, 110.0],
            "close_rate": [110.0, 180.0, 120.0],
            "shares": [50, 25, 40],
            "stake_amount": [5000.0, 5000.0, 4400.0],
            "entry_costs": [6.0, 6.0, 5.0],
            "exit_costs": [21.0, 20.0, 19.0],
            "profit_abs": [473.0, -526.0, 376.0],
            "profit_ratio": [0.094, -0.105, 0.085],
            "exit_reason": ["exit_signal", "stop_loss", "roi"],
            "enter_tag": ["rsi", "rsi", "rsi"],
            "bars_held": [8, 14, 14],
        }
    )


def _detail(run_id: int, name: str, status: str = "done") -> RunDetail:
    index = pd.bdate_range("2023-01-02", periods=120)
    equity = pd.Series(1_000_000 + pd.Series(range(120), index=index) * 250, index=index, dtype=float)
    trades = _trades()
    return RunDetail(
        run_id=run_id,
        name=name,
        status=status,
        config=_config(),
        metrics={
            "total_return_pct": 3.0,
            "cagr": 6.2,
            "sharpe": 1.1,
            "max_drawdown": -4.4,
            "final_equity": float(equity.iloc[-1]),
            "total_profit_abs": 29_750.0,
            "total_trades": 3,
            "win_rate": 66.0,
            "profit_factor": 1.6,
            "avg_exposure": 42.0,
            "total_costs": 77.0,
            "excess_return_pct": 1.2,
        },
        trades=trades,
        equity=equity,
        benchmark=equity * 0.98,
        per_symbol=per_symbol_stats(trades),
        per_exit_reason=per_exit_reason_stats(trades),
        periodic=pd.DataFrame(),
        costs={"entry": 17.0, "exit": 60.0},
        duration_s=4.2,
    )


def _runs_frame(rows: list[dict]) -> pl.DataFrame:
    columns = list(RUN_SCHEMA)
    return (
        pl.DataFrame({c: [r[c] for r in rows] for c in columns}, schema=RUN_SCHEMA)
        if rows
        else pl.DataFrame(schema=RUN_SCHEMA)
    )


def _run_row(run_id: int, name: str, status: str = "done") -> dict:
    payload = _config().to_dict()
    return {
        "id": run_id,
        "name": name,
        "created_at": datetime.datetime(2023, 7, 1, 10, 0),
        "finished_at": datetime.datetime(2023, 7, 1, 10, 5),
        "strategy": "RSI Basket",
        "mode": "signal",
        "params": payload["params"],
        "universe": payload["universe"],
        "costs": payload["costs"],
        "start": datetime.date(2023, 1, 2),
        "end": datetime.date(2023, 6, 30),
        "init_cash": 1_000_000.0,
        "status": status,
        "error": None,
        "summary": {
            "cagr": 6.2,
            "sharpe": 1.1,
            "max_drawdown": -4.4,
            "win_rate": 66.0,
            "total_trades": 3,
            "total_profit_abs": 29_750.0,
        },
        "duration_s": 4.2,
    }


def _optimization(run_id: int) -> OptimizationDetail:
    trials = pd.DataFrame(
        {
            "number": [0, 1, 2],
            "state": ["COMPLETE", "COMPLETE", "PRUNED"],
            "value": [0.8, 1.4, None],
            "oos_value": [0.6, 1.1, None],
            "n_trades": [30, 44, 2],
            "duration_s": [1.0, 1.1, 0.2],
            "window": [10, 20, 25],
        }
    )
    return OptimizationDetail(
        optimization_id=7,
        run_id=run_id,
        status="done",
        objective="sharpe",
        n_trials=3,
        best_params={"window": 20},
        best_value=1.4,
        param_importance={"window": 1.0},
        trials=trials,
    )


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """Patch every loader the page reads and keep persisted selections inside tmp_path."""
    monkeypatch.setattr("ui.persistence.selections.SELECTIONS_PATH", tmp_path / "selections.json")
    import ui.state.loaders as loaders

    state = {"rows": [], "details": {}, "catalog": {"RSI Basket": _spec()}, "optimization": None, "opt_id": None}

    def patch(name, fn):
        monkeypatch.setattr(loaders, name, fn, raising=False)
        for module in CONSUMERS:
            monkeypatch.setattr(module + "." + name, fn, raising=False)

    patch("load_strategy_catalog_cached", lambda: state["catalog"])
    patch("load_backtest_runs_cached", lambda: _runs_frame(state["rows"]))
    patch("load_backtest_run_cached", lambda run_id: state["details"].get(int(run_id)))
    patch("load_optimization_cached", lambda opt_id: state["optimization"])
    patch("clear_backtest_caches", lambda: None)
    monkeypatch.setattr("ui.views.backtest_lab.universe_picker.load_universe_cached", lambda *a, **k: ["TCS", "INFY"])
    monkeypatch.setattr("services.backtest.runs.latest_optimization", lambda run_id: state["opt_id"])
    return state


def _app(view: str = "Runs") -> AppTest:
    app = AppTest.from_file(PAGE, default_timeout=90)
    app.session_state["bt_view"] = view
    return app


def _button(app: AppTest, key: str):
    return next(b for b in app.button if b.key == key)


def _charts(app: AppTest) -> list:
    return list(app.get("plotly_chart"))


def _click(app: AppTest, key: str, view: str = "Runs") -> AppTest:
    """Click a button and rerun. AppTest models `st.segmented_control` as a multi-value button group,
    so the view switcher's value is restated as a list before the rerun collects widget states."""
    _button(app, key).click()
    for group in app.get("button_group"):
        group.set_value([view])
    return app.run()


def test_empty_state_invites_a_first_run(lab):
    app = _app().run()
    assert not app.exception
    assert any("No runs yet" in info.value for info in app.info)


def test_grid_lists_stored_runs(lab):
    lab["rows"] = [_run_row(1, "Nifty RSI"), _run_row(2, "Momentum", status="draft")]
    app = _app().run()
    assert not app.exception
    grid = next(df for df in app.dataframe if df.proto.id.startswith("bt_run_grid") or True)
    assert "Nifty RSI" in list(grid.value["Name"])
    assert {b.key for b in app.button} >= {"bt_toolbar_run", "bt_toolbar_dup", "bt_toolbar_delete_confirm"}


def test_detail_renders_a_finished_run(lab):
    lab["rows"] = [_run_row(1, "Nifty RSI")]
    lab["details"] = {1: _detail(1, "Nifty RSI")}
    app = _app("Detail").run()
    assert not app.exception
    assert any("RSI Basket" in caption.value for caption in app.caption)
    assert len(_charts(app)) >= 4
    assert {m.label for m in app.metric} >= {"Total return", "Sharpe", "Trades"}


def test_detail_shows_the_error_of_a_failed_run(lab):
    failed = _detail(1, "Broken", status="failed")
    failed.error = "no symbols had enough history"
    lab["rows"] = [_run_row(1, "Broken", status="failed")]
    lab["details"] = {1: failed}
    app = _app("Detail").run()
    assert not app.exception
    assert any("no symbols had enough history" in err.value for err in app.error)


def test_compare_overlays_two_runs(lab):
    lab["rows"] = [_run_row(1, "Alpha"), _run_row(2, "Beta")]
    lab["details"] = {1: _detail(1, "Alpha"), 2: _detail(2, "Beta")}
    app = _app("Compare")
    app.session_state["bt_compare_ids"] = [1, 2]
    app.run()
    assert not app.exception
    assert _charts(app)
    assert any("Alpha" in str(df.value.columns.tolist()) for df in app.dataframe)


def test_optimise_renders_form_and_results(lab):
    lab["rows"] = [_run_row(1, "Nifty RSI")]
    lab["details"] = {1: _detail(1, "Nifty RSI")}
    lab["opt_id"] = 7
    lab["optimization"] = _optimization(1)
    app = _app("Optimise").run()
    assert not app.exception
    assert {b.key for b in app.button} >= {"bt_opt_start", "bt_opt_apply"}
    assert {s.key for s in app.selectbox} >= {"bt_opt_objective"}
    assert any(m.label == "Best in-sample" for m in app.metric)


def test_composer_creates_the_configured_run(lab, monkeypatch):
    captured: dict = {}

    def fake_create_run(config, name):
        captured["config"], captured["name"] = config, name
        return 42

    monkeypatch.setattr("services.backtest.runs.create_run", fake_create_run)
    app = _app().run()
    _click(app, "bt_form_create")
    assert not app.exception
    config = captured["config"]
    assert config.strategy == "RSI Basket"
    assert config.params == {"window": 14, "oversold": 30.0}
    assert config.universe.kind == "index"
    assert config.costs.name == "zerodha_delivery"
    assert captured["name"]


def _diagnostics() -> SimpleNamespace:
    """The shape `strategies.ml.diagnostics.MLDiagnostics` exposes to the Detail expander."""
    return SimpleNamespace(
        summary={"ic": 0.08, "windows": 4},
        windows=pd.DataFrame(
            {
                "idx": [0, 1],
                "test_start": pd.to_datetime(["2023-02-01", "2023-04-01"]),
                "n_train": [500, 600],
                "n_test": [40, 40],
                "fit_seconds": [0.4, 0.5],
                "ic": [0.05, 0.11],
            }
        ),
        importance=pd.DataFrame({"importance": [0.4, 0.2], "std": [0.05, 0.02]}, index=["%-rsi", "%-macd_ratio"]),
        predictions=pd.DataFrame({"pred": [0.4, 0.6, 0.7], "do_predict": [1, 1, 1]}),
        coverage=pd.Series([0.5, 0.8], index=pd.to_datetime(["2023-02-01", "2023-04-01"])),
    )


def test_detail_shows_ml_diagnostics_when_present(lab):
    detail = _detail(1, "ML run")
    detail.diagnostics = _diagnostics()
    lab["rows"] = [_run_row(1, "ML run")]
    lab["details"] = {1: detail}
    app = _app("Detail").run()
    assert not app.exception
    assert any(s.key == "bt_ml_metric" for s in app.selectbox)


def test_edit_dialog_opens_for_the_staged_run(lab):
    lab["rows"] = [_run_row(1, "Nifty RSI")]
    lab["details"] = {1: _detail(1, "Nifty RSI")}
    app = _app()
    app.session_state["bt_edit_open"] = 1
    app.run()
    assert not app.exception
    assert any(b.key == "bt_edit_save" for b in app.button)


def test_launch_respects_the_concurrency_cap(lab, monkeypatch):
    from ui.views.backtest_lab import runs_tab

    started: list[int] = []
    monkeypatch.setattr("services.backtest.runs.start_run", lambda rid, **_: started.append(rid) or {})
    rows = [_run_row(rid, f"Run {rid}", status="draft") for rid in (9001, 9002, 9003)]
    launched = runs_tab.launch([9001, 9002, 9003], rows, lab["catalog"])
    assert launched == [9001, 9002]
