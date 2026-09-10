from datetime import date

import pandas as pd
import pytest

from services.backtest import runs
from services.backtest.config import BacktestConfig, UniverseSpec
from services.backtest.engine import BacktestResult


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy="RSI Basket",
        params={"window": 14},
        universe=UniverseSpec(kind="list", symbols=("TCS", "INFY")),
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
    )


def _result(config: BacktestConfig) -> BacktestResult:
    index = pd.date_range("2024-01-01", periods=3, freq="D")
    equity = pd.Series([100.0, 110.0, 120.0], index=index)
    trades = pd.DataFrame(
        {
            "symbol": ["TCS"],
            "open_date": [pd.Timestamp("2024-01-01")],
            "close_date": [pd.Timestamp("2024-01-03")],
            "open_rate": [100.0],
            "close_rate": [110.0],
            "shares": [10],
            "stake_amount": [1000.0],
            "entry_costs": [1.0],
            "exit_costs": [1.0],
            "profit_abs": [98.0],
            "profit_ratio": [0.098],
            "exit_reason": ["exit_signal"],
            "enter_tag": ["rsi"],
            "exit_tag": [None],
            "bars_held": [2],
            "max_rate": [111.0],
            "min_rate": [99.0],
        }
    )
    zeros = pd.Series([0.0, 0.0, 0.0], index=index)
    return BacktestResult(
        trades=trades,
        equity=equity,
        cash=zeros,
        invested=zeros,
        exposure=zeros,
        open_positions=pd.Series([0, 1, 0], index=index),
        returns=equity.pct_change().fillna(0.0),
        benchmark_returns=None,
        per_symbol=pd.DataFrame(),
        per_exit_reason=pd.DataFrame(),
        metrics={"final_equity": 120.0, "sharpe": 1.4, "total_trades": 1},
        config=config,
        strategy_name="RSI Basket",
        params={"window": 14},
    )


@pytest.fixture
def fake_repo(monkeypatch):
    store: dict = {"runs": {}, "trades": {}, "next_id": 1}

    def save_run(**kwargs):
        run_id = store["next_id"]
        store["next_id"] += 1
        kwargs.setdefault("status", "draft")
        store["runs"][run_id] = {"id": run_id, **kwargs, "summary": None, "equity": None, "error": None}
        return run_id

    def save_status(run_id, status, *, error=None):
        store["runs"][run_id]["status"] = status
        store["runs"][run_id]["error"] = error

    def save_result(run_id, *, summary, equity, trades, duration_s=None):
        store["runs"][run_id].update(summary=summary, equity=equity, status="done", duration_s=duration_s)
        store["trades"][run_id] = trades

    monkeypatch.setattr(runs.repo, "save_backtest_run", save_run)
    monkeypatch.setattr(runs.repo, "save_backtest_run_status", save_status)
    monkeypatch.setattr(runs.repo, "save_backtest_result", save_result)
    monkeypatch.setattr(runs.repo, "get_backtest_run", lambda run_id: store["runs"].get(run_id))
    return store


def test_create_run_stores_a_draft_with_the_config_echo(fake_repo):
    run_id = runs.create_run(_config(), "First run")
    row = fake_repo["runs"][run_id]
    assert row["status"] == "draft"
    assert row["name"] == "First run"
    assert row["strategy"] == "RSI Basket"
    assert row["mode"] == "signal"
    assert row["config"]["start"] == "2024-01-01"
    assert row["params"] == {"window": 14}


def test_start_run_walks_queued_running_done_and_saves_trades(fake_repo, monkeypatch):
    config = _config()
    run_id = runs.create_run(config, "First run")
    seen = []
    monkeypatch.setattr(runs, "load_backtest_inputs", lambda *a, **k: object())
    monkeypatch.setattr(runs, "run_engine", lambda *a, **k: _result(config))
    monkeypatch.setattr(runs.repo, "save_backtest_run_status", lambda rid, status, **kw: seen.append(status))

    summary = runs.start_run(run_id)

    assert seen == ["queued", "running"]
    assert fake_repo["runs"][run_id]["status"] == "done"
    assert summary["total_trades"] == 1
    assert fake_repo["trades"][run_id][0]["symbol"] == "TCS"
    assert fake_repo["trades"][run_id][0]["open_date"] == date(2024, 1, 1)
    assert fake_repo["runs"][run_id]["equity"]["dates"][0] == "2024-01-01"
    assert fake_repo["runs"][run_id]["duration_s"] >= 0


def test_start_run_records_the_error_and_reraises(fake_repo, monkeypatch):
    run_id = runs.create_run(_config(), "Doomed")

    def explode(*args, **kwargs):
        raise RuntimeError("no data")

    monkeypatch.setattr(runs, "load_backtest_inputs", explode)
    with pytest.raises(RuntimeError):
        runs.start_run(run_id)
    row = fake_repo["runs"][run_id]
    assert row["status"] == "failed"
    assert "no data" in row["error"]


def test_duplicate_run_copies_the_config_and_applies_overrides(fake_repo):
    run_id = runs.create_run(_config(), "Original")
    copy_id = runs.duplicate_run(run_id, {"name": "Copy", "params": {"window": 21}})
    row = fake_repo["runs"][copy_id]
    assert row["name"] == "Copy"
    assert row["params"] == {"window": 21}
    assert row["status"] == "draft"
    assert row["config"]["universe"] == fake_repo["runs"][run_id]["config"]["universe"]


def test_strategy_catalog_exposes_declared_parameters_and_attributes():
    catalog = runs.strategy_catalog()
    spec = catalog["RSI Basket"]
    assert spec.mode == "signal"
    assert spec.parameters["window"].default == 14
    assert spec.stoploss == pytest.approx(-0.10)
    assert catalog["Momentum Rank"].mode == "rank"
    assert catalog["Momentum Rank"].top_k == 10


def test_task_key_is_stable():
    assert runs.task_key(7) == "bt_run_7"


def test_ml_diagnostics_survive_a_save_and_load_round_trip():
    """The detail page reads diagnostics back from the stored run, so a run that trained models
    must carry its windows, importance, coverage and prediction sample."""
    import numpy as np

    from strategies.ml.diagnostics import MLDiagnostics

    diagnostics = MLDiagnostics(
        summary={"kind": "classifier", "n_windows": 2, "auc_mean": np.float64(0.53)},
        windows=pd.DataFrame(
            {
                "idx": [0, 1],
                "test_start": pd.to_datetime(["2024-01-01", "2024-04-01"]),
                "auc": [0.51, 0.55],
                "n_train": [500, 500],
            }
        ),
        importance=pd.DataFrame(
            {"importance": [0.4, 0.1], "std": [0.05, 0.02]}, index=pd.Index(["%-rsi", "%-natr"], name="feature")
        ),
        predictions=pd.DataFrame({"pred": [0.4, 0.6, 0.55]}),
        coverage=pd.Series({"2024-01-01": 0.8}),
    )

    payload = runs.diagnostics_payload(diagnostics)
    restored = runs.diagnostics_from_payload(payload)

    assert restored.summary["kind"] == "classifier"
    assert list(restored.windows["auc"]) == [0.51, 0.55]
    assert restored.windows["test_start"].dtype.kind == "M"
    assert restored.importance.index.tolist() == ["%-rsi", "%-natr"]
    assert restored.importance["importance"].tolist() == [0.4, 0.1]
    assert restored.predictions["pred"].tolist() == [0.4, 0.6, 0.55]
    assert runs.diagnostics_from_payload(None) is None


def test_start_run_stores_diagnostics_under_the_summary(fake_repo, monkeypatch):
    config = _config()
    run_id = runs.create_run(config, "ML run")
    result = _result(config)
    result.diagnostics = object()
    monkeypatch.setattr(runs, "load_backtest_inputs", lambda *a, **k: object())
    monkeypatch.setattr(runs, "run_engine", lambda *a, **k: result)
    monkeypatch.setattr(runs, "diagnostics_payload", lambda d: {"summary": {"kind": "classifier"}})

    runs.start_run(run_id)

    assert fake_repo["runs"][run_id]["summary"]["ml_diagnostics"]["summary"]["kind"] == "classifier"
