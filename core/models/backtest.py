"""Backtest Lab persistence: runs, their trades, optimisations and trials."""

import datetime

from sqlalchemy import JSON, Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel

from core.constants import TABLE_ARGS


class BacktestRun(SQLModel, table=True):
    """One configured run. `config` is the full BacktestConfig echo; `summary` and `equity` are
    filled when the run finishes."""

    __tablename__ = "backtest_run"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    name: str
    created_at: datetime.datetime
    finished_at: datetime.datetime | None = None
    strategy: str
    mode: str
    params: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    universe: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    costs: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    config: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    start: datetime.date
    end: datetime.date
    init_cash: float
    status: str = "draft"
    error: str | None = None
    summary: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    equity: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    duration_s: float | None = None


class BacktestTrade(SQLModel, table=True):
    __tablename__ = "backtest_trade"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(sa_column=Column(Integer, ForeignKey("backtest_run.id", ondelete="CASCADE"), index=True))
    symbol: str
    open_date: datetime.date
    close_date: datetime.date
    open_rate: float
    close_rate: float
    shares: int
    stake_amount: float
    entry_costs: float
    exit_costs: float
    profit_abs: float
    profit_ratio: float
    exit_reason: str
    enter_tag: str | None = None
    bars_held: int


class BacktestOptimization(SQLModel, table=True):
    __tablename__ = "backtest_optimization"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(sa_column=Column(Integer, ForeignKey("backtest_run.id", ondelete="CASCADE"), index=True))
    created_at: datetime.datetime
    finished_at: datetime.datetime | None = None
    objective: str
    n_trials: int
    status: str = "running"
    error: str | None = None
    spec: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    best_params: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    best_value: float | None = None
    param_importance: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    is_end: datetime.date | None = None
    oos_start: datetime.date | None = None


class BacktestTrial(SQLModel, table=True):
    __tablename__ = "backtest_trial"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    optimization_id: int = Field(
        sa_column=Column(Integer, ForeignKey("backtest_optimization.id", ondelete="CASCADE"), index=True)
    )
    number: int
    state: str
    params: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    value: float | None = None
    n_trades: int = 0
    is_metrics: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    oos_metrics: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    oos_value: float | None = None
    duration_s: float = 0.0
