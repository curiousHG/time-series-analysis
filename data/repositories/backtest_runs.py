"""Backtest Lab CRUD: runs, trades, optimisations, trials. All SQL for the feature lives here."""

from __future__ import annotations

import datetime
import json
import logging

import polars as pl
from sqlmodel import col, delete, select

from core.database import get_session
from core.frames import frame_from_rows
from core.models.backtest import BacktestOptimization, BacktestRun, BacktestTrade, BacktestTrial

logger = logging.getLogger(__name__)

TRADE_SCHEMA = {
    "symbol": pl.Utf8,
    "open_date": pl.Date,
    "close_date": pl.Date,
    "open_rate": pl.Float64,
    "close_rate": pl.Float64,
    "shares": pl.Int64,
    "stake_amount": pl.Float64,
    "entry_costs": pl.Float64,
    "exit_costs": pl.Float64,
    "profit_abs": pl.Float64,
    "profit_ratio": pl.Float64,
    "exit_reason": pl.Utf8,
    "enter_tag": pl.Utf8,
    "bars_held": pl.Int64,
}

RUN_SCHEMA = {
    "id": pl.Int64,
    "name": pl.Utf8,
    "created_at": pl.Datetime,
    "finished_at": pl.Datetime,
    "strategy": pl.Utf8,
    "mode": pl.Utf8,
    "params": pl.Utf8,
    "universe": pl.Utf8,
    "costs": pl.Utf8,
    "start": pl.Date,
    "end": pl.Date,
    "init_cash": pl.Float64,
    "status": pl.Utf8,
    "error": pl.Utf8,
    "summary": pl.Utf8,
    "duration_s": pl.Float64,
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def save_backtest_run(
    *,
    name: str,
    strategy: str,
    mode: str,
    config: dict,
    params: dict,
    universe: dict,
    costs: dict,
    start: datetime.date,
    end: datetime.date,
    init_cash: float,
    status: str = "draft",
) -> int:
    row = BacktestRun(
        name=name,
        created_at=_now(),
        strategy=strategy,
        mode=mode,
        params=params,
        universe=universe,
        costs=costs,
        config=config,
        start=start,
        end=end,
        init_cash=init_cash,
        status=status,
    )
    with get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def update_backtest_run(run_id: int, *, name: str, config: dict, params: dict, universe: dict, costs: dict) -> None:
    with get_session() as session:
        row = session.get(BacktestRun, run_id)
        if row is None:
            raise KeyError(run_id)
        if row.status != "draft":
            raise ValueError(f"run {run_id} is {row.status}; only drafts can be edited")
        row.name = name
        row.config = config
        row.params = params
        row.universe = universe
        row.costs = costs
        row.strategy = config["strategy"]
        row.start = datetime.date.fromisoformat(config["start"])
        row.end = datetime.date.fromisoformat(config["end"])
        row.init_cash = float(config["init_cash"])
        session.add(row)
        session.commit()


def save_backtest_run_status(run_id: int, status: str, *, error: str | None = None) -> None:
    with get_session() as session:
        row = session.get(BacktestRun, run_id)
        if row is None:
            raise KeyError(run_id)
        row.status = status
        row.error = error
        if status in ("done", "failed"):
            row.finished_at = _now()
        if status == "queued":
            row.error = None
            row.finished_at = None
        session.add(row)
        session.commit()


def save_backtest_result(
    run_id: int, *, summary: dict, equity: dict, trades: list[dict], duration_s: float | None = None
) -> None:
    with get_session() as session:
        row = session.get(BacktestRun, run_id)
        if row is None:
            raise KeyError(run_id)
        session.exec(delete(BacktestTrade).where(col(BacktestTrade.run_id) == run_id))
        for trade in trades:
            session.add(BacktestTrade(run_id=run_id, **{k: trade.get(k) for k in TRADE_SCHEMA}))
        row.summary = summary
        row.equity = equity
        row.status = "done"
        row.error = None
        row.finished_at = _now()
        row.duration_s = duration_s
        session.add(row)
        session.commit()


JSON_COLUMNS = ("params", "universe", "costs", "summary")


def load_backtest_runs() -> pl.DataFrame:
    """Every run, newest first. The JSON columns come back as strings: a polars frame of dicts
    cannot be pickled, and the UI caches this frame."""
    with get_session() as session:
        rows = session.exec(select(BacktestRun).order_by(col(BacktestRun.created_at).desc())).all()
    records = [
        tuple(
            json.dumps(getattr(r, name)) if name in JSON_COLUMNS and getattr(r, name) is not None else getattr(r, name)
            for name in RUN_SCHEMA
        )
        for r in rows
    ]
    return frame_from_rows(records, RUN_SCHEMA)


def get_backtest_run(run_id: int) -> dict | None:
    with get_session() as session:
        row = session.get(BacktestRun, run_id)
        return None if row is None else row.model_dump()


def load_backtest_trades(run_id: int) -> pl.DataFrame:
    with get_session() as session:
        rows = session.exec(
            select(BacktestTrade)
            .where(col(BacktestTrade.run_id) == run_id)
            .order_by(col(BacktestTrade.open_date), col(BacktestTrade.symbol))
        ).all()
    records = [tuple(getattr(r, name) for name in TRADE_SCHEMA) for r in rows]
    return frame_from_rows(records, TRADE_SCHEMA)


def delete_backtest_run(run_id: int) -> None:
    with get_session() as session:
        opt_ids = session.exec(select(BacktestOptimization.id).where(col(BacktestOptimization.run_id) == run_id)).all()
        if opt_ids:
            session.exec(delete(BacktestTrial).where(col(BacktestTrial.optimization_id).in_(list(opt_ids))))
        session.exec(delete(BacktestOptimization).where(col(BacktestOptimization.run_id) == run_id))
        session.exec(delete(BacktestTrade).where(col(BacktestTrade.run_id) == run_id))
        session.exec(delete(BacktestRun).where(col(BacktestRun.id) == run_id))
        session.commit()


def save_optimization(*, run_id: int, objective: str, n_trials: int, spec: dict, status: str = "running") -> int:
    row = BacktestOptimization(
        run_id=run_id, created_at=_now(), objective=objective, n_trials=n_trials, spec=spec, status=status
    )
    with get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


def save_optimization_result(
    optimization_id: int,
    *,
    status: str,
    best_params: dict | None,
    best_value: float | None,
    param_importance: dict | None,
    is_end: datetime.date | None,
    oos_start: datetime.date | None,
    trials: list[dict],
    error: str | None = None,
) -> None:
    with get_session() as session:
        row = session.get(BacktestOptimization, optimization_id)
        if row is None:
            raise KeyError(optimization_id)
        session.exec(delete(BacktestTrial).where(col(BacktestTrial.optimization_id) == optimization_id))
        for trial in trials:
            session.add(BacktestTrial(optimization_id=optimization_id, **trial))
        row.status = status
        row.error = error
        row.best_params = best_params
        row.best_value = best_value
        row.param_importance = param_importance
        row.is_end = is_end
        row.oos_start = oos_start
        row.finished_at = _now()
        session.add(row)
        session.commit()


def load_optimizations(run_id: int | None = None) -> list[dict]:
    with get_session() as session:
        stmt = select(BacktestOptimization).order_by(col(BacktestOptimization.created_at).desc())
        if run_id is not None:
            stmt = stmt.where(col(BacktestOptimization.run_id) == run_id)
        return [r.model_dump() for r in session.exec(stmt).all()]


def get_optimization(optimization_id: int) -> dict | None:
    with get_session() as session:
        row = session.get(BacktestOptimization, optimization_id)
        return None if row is None else row.model_dump()


def load_trials(optimization_id: int) -> list[dict]:
    with get_session() as session:
        rows = session.exec(
            select(BacktestTrial)
            .where(col(BacktestTrial.optimization_id) == optimization_id)
            .order_by(col(BacktestTrial.number))
        ).all()
        return [r.model_dump(exclude={"id", "optimization_id"}) for r in rows]
