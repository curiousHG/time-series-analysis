"""Round trip of the Backtest Lab repository on an in-memory SQLite database."""

from datetime import date

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from core.models.backtest import BacktestOptimization, BacktestRun, BacktestTrade, BacktestTrial
from data.repositories import backtest_runs as repo


@pytest.fixture
def sqlite_repo(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(
        engine,
        tables=[
            BacktestRun.__table__,
            BacktestTrade.__table__,
            BacktestOptimization.__table__,
            BacktestTrial.__table__,
        ],
    )
    monkeypatch.setattr(repo, "get_session", lambda: Session(engine))
    return repo


def _config() -> dict:
    return {
        "strategy": "RSI Basket",
        "params": {"window": 14},
        "universe": {"kind": "index", "name": "NIFTY 50", "symbols": []},
        "start": "2020-01-01",
        "end": "2024-12-31",
        "init_cash": 1_000_000.0,
        "costs": {"name": "zerodha_delivery"},
        "slippage_bps": 5.0,
        "strategy_overrides": {},
    }


def _save(repo_module) -> int:
    cfg = _config()
    return repo_module.save_backtest_run(
        name="RSI on Nifty",
        strategy=cfg["strategy"],
        mode="signal",
        config=cfg,
        params=cfg["params"],
        universe=cfg["universe"],
        costs=cfg["costs"],
        start=date(2020, 1, 1),
        end=date(2024, 12, 31),
        init_cash=cfg["init_cash"],
    )


def test_run_lifecycle_draft_to_done(sqlite_repo):
    run_id = _save(sqlite_repo)
    runs = sqlite_repo.load_backtest_runs()
    assert runs["status"].to_list() == ["draft"]
    assert runs["name"].to_list() == ["RSI on Nifty"]

    sqlite_repo.save_backtest_run_status(run_id, "queued")
    sqlite_repo.save_backtest_run_status(run_id, "running")
    trades = [
        {
            "symbol": "TCS",
            "open_date": date(2021, 1, 4),
            "close_date": date(2021, 2, 1),
            "open_rate": 100.0,
            "close_rate": 110.0,
            "shares": 10,
            "stake_amount": 1000.0,
            "entry_costs": 1.2,
            "exit_costs": 1.3,
            "profit_abs": 97.5,
            "profit_ratio": 0.0974,
            "exit_reason": "exit_signal",
            "enter_tag": "rsi",
            "bars_held": 19,
        }
    ]
    sqlite_repo.save_backtest_result(
        run_id,
        summary={"cagr": 12.5},
        equity={"dates": ["2021-01-04"], "equity": [1000.0]},
        trades=trades,
        duration_s=4.2,
    )

    run = sqlite_repo.get_backtest_run(run_id)
    assert run["status"] == "done"
    assert run["summary"] == {"cagr": 12.5}
    assert run["finished_at"] is not None
    assert run["duration_s"] == 4.2
    loaded = sqlite_repo.load_backtest_trades(run_id)
    assert loaded.height == 1
    assert loaded["symbol"].to_list() == ["TCS"]
    assert loaded["profit_abs"].to_list() == [97.5]


def test_failed_run_keeps_error_and_relaunch_clears_it(sqlite_repo):
    run_id = _save(sqlite_repo)
    sqlite_repo.save_backtest_run_status(run_id, "failed", error="boom")
    assert sqlite_repo.get_backtest_run(run_id)["error"] == "boom"
    sqlite_repo.save_backtest_run_status(run_id, "queued")
    run = sqlite_repo.get_backtest_run(run_id)
    assert run["error"] is None and run["finished_at"] is None


def test_only_drafts_can_be_edited(sqlite_repo):
    run_id = _save(sqlite_repo)
    cfg = {**_config(), "start": "2021-01-01", "params": {"window": 21}}
    sqlite_repo.update_backtest_run(
        run_id, name="Edited", config=cfg, params=cfg["params"], universe=cfg["universe"], costs=cfg["costs"]
    )
    run = sqlite_repo.get_backtest_run(run_id)
    assert run["name"] == "Edited" and run["params"] == {"window": 21} and str(run["start"]) == "2021-01-01"
    sqlite_repo.save_backtest_run_status(run_id, "done")
    with pytest.raises(ValueError):
        sqlite_repo.update_backtest_run(run_id, name="x", config=cfg, params={}, universe={}, costs={})


def test_delete_cascades_to_trades_optimizations_and_trials(sqlite_repo):
    run_id = _save(sqlite_repo)
    sqlite_repo.save_backtest_result(run_id, summary={}, equity={}, trades=[])
    opt_id = sqlite_repo.save_optimization(run_id=run_id, objective="sharpe", n_trials=3, spec={"n_trials": 3})
    sqlite_repo.save_optimization_result(
        opt_id,
        status="done",
        best_params={"window": 10},
        best_value=1.2,
        param_importance={"window": 1.0},
        is_end=None,
        oos_start=None,
        trials=[
            {
                "number": 0,
                "state": "COMPLETE",
                "params": {"window": 10},
                "value": 1.2,
                "n_trades": 30,
                "duration_s": 0.5,
            }
        ],
    )
    assert sqlite_repo.load_trials(opt_id)[0]["params"] == {"window": 10}
    assert sqlite_repo.load_optimizations(run_id)[0]["best_value"] == 1.2

    sqlite_repo.delete_backtest_run(run_id)
    assert sqlite_repo.get_backtest_run(run_id) is None
    assert sqlite_repo.load_backtest_trades(run_id).height == 0
    assert sqlite_repo.load_optimizations(run_id) == []
    assert sqlite_repo.load_trials(opt_id) == []
