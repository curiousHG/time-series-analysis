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
    volume: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    turnover: float | None = None
    num_trades: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class IndexOhlcv(SQLModel, table=True):
    """Daily OHLC for market indices (Nifty 50, Bank Nifty, Sensex, …). Split from stock_ohlcv
    because indices have no ISIN and thinner data — keyed by the index symbol (^NSEI,
    "NIFTY SMALLCAP 250"). Volume is null for indices."""

    __tablename__ = "index_ohlcv"
    __table_args__ = TABLE_ARGS

    date: datetime.date = Field(primary_key=True)
    symbol: str = Field(primary_key=True, index=True)
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    turnover_cr: float | None = None
    pe: float | None = None
    pb: float | None = None
    div_yield: float | None = None


class IndexRegistry(SQLModel, table=True):
    """Light registry for indices we hold OHLC for — a display name plus the backward-gap
    floor (earliest) so ensure_index_data stops re-attempting pre-inception history."""

    __tablename__ = "index_registry"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True)
    name: str | None = None
    status: str | None = None
    as_of: datetime.datetime | None = None
    earliest: datetime.date | None = None


class StockRegistry(SQLModel, table=True):
    __tablename__ = "stock_registry"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True)
    stock_name: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
    isin: str | None = None
    series: str | None = None
    fundamentals_status: str | None = None
    fundamentals_as_of: datetime.datetime | None = None
    ohlcv_status: str | None = None
    ohlcv_as_of: datetime.datetime | None = None
    ohlcv_earliest: datetime.date | None = None
    ohlcv_empty_streak: int | None = None


class StockQuarterly(SQLModel, table=True):
    """Quarterly P&L per symbol, scraped from screener.in's Quarterly Results table."""

    __tablename__ = "stock_quarterly"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True, index=True)
    period_end: datetime.date = Field(primary_key=True)
    period_label: str | None = None
    sales: float | None = None
    expenses: float | None = None
    operating_profit: float | None = None
    opm_pct: float | None = None
    other_income: float | None = None
    interest: float | None = None
    depreciation: float | None = None
    profit_before_tax: float | None = None
    tax_pct: float | None = None
    net_profit: float | None = None
    eps: float | None = None


class StockMetrics(SQLModel, table=True):
    """Screener-shaped per-symbol snapshot: screener.in top ratios + derived growth/ownership."""

    __tablename__ = "stock_metrics"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True)
    market_cap: float | None = None
    current_price: float | None = None
    stock_pe: float | None = None
    book_value: float | None = None
    dividend_yield: float | None = None
    roce: float | None = None
    roe: float | None = None
    face_value: float | None = None
    last_quarter_label: str | None = None
    sales_latest_q: float | None = None
    net_profit_latest_q: float | None = None
    opm_latest_q: float | None = None
    eps_latest_q: float | None = None
    yoy_sales_growth: float | None = None
    yoy_profit_growth: float | None = None
    qoq_sales_growth: float | None = None
    qoq_profit_growth: float | None = None
    promoter_holding: float | None = None
    promoter_holding_change_1y: float | None = None
    return_1y: float | None = None
    vol_1y: float | None = None
    beta_1y: float | None = None
    alpha_1y: float | None = None
    r2_1y: float | None = None
    computed_at: datetime.datetime | None = None
