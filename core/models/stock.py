"""SQLModel ORM models for stock data."""

import datetime

from sqlmodel import Field, SQLModel

TABLE_ARGS = {"extend_existing": True}


class StockOhlcv(SQLModel, table=True):
    __tablename__ = "stock_ohlcv"
    __table_args__ = TABLE_ARGS

    date: datetime.date = Field(primary_key=True)
    symbol: str = Field(primary_key=True, index=True)
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None


class StockRegistry(SQLModel, table=True):
    __tablename__ = "stock_registry"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True)
    stock_name: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
