"""SQLModel ORM models for the trading bot framework."""

import datetime

from sqlmodel import Field, SQLModel

TABLE_ARGS = {"extend_existing": True}


class Bot(SQLModel, table=True):
    __tablename__ = "bots"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    strategy: str
    exchange: str = "paper"
    symbol: str | None = None
    timeframe: str = "1d"
    state: str = "stopped"  # BotState enum value
    config_json: str | None = None
    stake_amount: float = 10_000.0
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)


class Trade(SQLModel, table=True):
    __tablename__ = "trades"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    symbol: str
    direction: str = "long"  # TradeDirection enum value
    entry_price: float | None = None
    exit_price: float | None = None
    quantity: float = 0.0
    pnl: float | None = None
    return_pct: float | None = None
    status: str = "open"
    opened_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    closed_at: datetime.datetime | None = None


class Order(SQLModel, table=True):
    __tablename__ = "orders"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    trade_id: int | None = Field(default=None, index=True)
    bot_id: int = Field(index=True)
    symbol: str
    side: str  # OrderSide enum value
    order_type: str = "market"  # OrderType enum value
    price: float | None = None
    quantity: float = 0.0
    filled_quantity: float = 0.0
    status: str = "pending"  # OrderStatus enum value
    exchange_order_id: str | None = None
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    filled_at: datetime.datetime | None = None
