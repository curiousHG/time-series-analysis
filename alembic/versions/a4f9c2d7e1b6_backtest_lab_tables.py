"""Backtest Lab tables: runs, trades, optimisations, trials.

Guarded so a database that already received the tables from `create_all` on boot is a no-op.

Revision ID: a4f9c2d7e1b6
Revises: g8b4d7e9f2c3
Create Date: 2026-09-09 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a4f9c2d7e1b6"
down_revision = "g8b4d7e9f2c3"
branch_labels = None
depends_on = None

_TABLES = ("backtest_trial", "backtest_optimization", "backtest_trade", "backtest_run")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    have = _tables()
    if "backtest_run" not in have:
        op.create_table(
            "backtest_run",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("strategy", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("universe", sa.JSON(), nullable=False),
            sa.Column("costs", sa.JSON(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("start", sa.Date(), nullable=False),
            sa.Column("end", sa.Date(), nullable=False),
            sa.Column("init_cash", sa.Float(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.Column("equity", sa.JSON(), nullable=True),
            sa.Column("duration_s", sa.Float(), nullable=True),
        )
    if "backtest_trade" not in have:
        op.create_table(
            "backtest_trade",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_run.id", ondelete="CASCADE"), nullable=False),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("open_date", sa.Date(), nullable=False),
            sa.Column("close_date", sa.Date(), nullable=False),
            sa.Column("open_rate", sa.Float(), nullable=False),
            sa.Column("close_rate", sa.Float(), nullable=False),
            sa.Column("shares", sa.Integer(), nullable=False),
            sa.Column("stake_amount", sa.Float(), nullable=False),
            sa.Column("entry_costs", sa.Float(), nullable=False),
            sa.Column("exit_costs", sa.Float(), nullable=False),
            sa.Column("profit_abs", sa.Float(), nullable=False),
            sa.Column("profit_ratio", sa.Float(), nullable=False),
            sa.Column("exit_reason", sa.String(), nullable=False),
            sa.Column("enter_tag", sa.String(), nullable=True),
            sa.Column("bars_held", sa.Integer(), nullable=False),
        )
        op.create_index("ix_backtest_trade_run_id", "backtest_trade", ["run_id"])
    if "backtest_optimization" not in have:
        op.create_table(
            "backtest_optimization",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_run.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("objective", sa.String(), nullable=False),
            sa.Column("n_trials", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("spec", sa.JSON(), nullable=False),
            sa.Column("best_params", sa.JSON(), nullable=True),
            sa.Column("best_value", sa.Float(), nullable=True),
            sa.Column("param_importance", sa.JSON(), nullable=True),
            sa.Column("is_end", sa.Date(), nullable=True),
            sa.Column("oos_start", sa.Date(), nullable=True),
        )
        op.create_index("ix_backtest_optimization_run_id", "backtest_optimization", ["run_id"])
    if "backtest_trial" not in have:
        op.create_table(
            "backtest_trial",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "optimization_id",
                sa.Integer(),
                sa.ForeignKey("backtest_optimization.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("n_trades", sa.Integer(), nullable=False),
            sa.Column("is_metrics", sa.JSON(), nullable=True),
            sa.Column("oos_metrics", sa.JSON(), nullable=True),
            sa.Column("oos_value", sa.Float(), nullable=True),
            sa.Column("duration_s", sa.Float(), nullable=False),
        )
        op.create_index("ix_backtest_trial_optimization_id", "backtest_trial", ["optimization_id"])


def downgrade() -> None:
    have = _tables()
    for table in _TABLES:
        if table in have:
            op.drop_table(table)
