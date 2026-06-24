"""SQLModel ORM models for stock data."""

import datetime

from sqlalchemy import BigInteger, Column
from sqlmodel import Field, SQLModel

from core.constants import TABLE_ARGS


class StockOhlcv(SQLModel, table=True):
    __tablename__ = "stock_ohlcv"
    __table_args__ = TABLE_ARGS

    date: datetime.date = Field(primary_key=True)
    symbol: str = Field(primary_key=True, index=True)
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    # BigInteger required — index volumes (Nifty 100, etc.) routinely exceed 32-bit INTEGER's ~2.1B max.
    volume: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class StockRegistry(SQLModel, table=True):
    __tablename__ = "stock_registry"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True)
    stock_name: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
