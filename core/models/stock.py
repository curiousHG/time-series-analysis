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
    # From the NSE bhavcopy (both schemas): traded value in ₹ + number of trades that day.
    turnover: float | None = None  # total traded value in ₹
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
    # From the NSE index bhavcopy (ind_close_all) — valuation + traded value per index.
    turnover_cr: float | None = None  # turnover in ₹ crore
    pe: float | None = None
    pb: float | None = None
    div_yield: float | None = None


class IndexRegistry(SQLModel, table=True):
    """Light registry for indices we hold OHLC for — a display name plus the backward-gap
    floor (earliest) so ensure_index_data stops re-attempting pre-inception history."""

    __tablename__ = "index_registry"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True)
    name: str | None = None  # display label, e.g. "Nifty 50"
    status: str | None = None  # available / unavailable
    as_of: datetime.datetime | None = None
    earliest: datetime.date | None = None  # confirmed earliest-available date (backward-gap floor)


class StockRegistry(SQLModel, table=True):
    __tablename__ = "stock_registry"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True)  # bare NSE symbol (RELIANCE); ISIN is the durable unique id
    stock_name: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
    isin: str | None = None
    series: str | None = None  # NSE series, e.g. "EQ"
    fundamentals_status: str | None = None  # pending / available / unavailable
    fundamentals_as_of: datetime.datetime | None = None
    # OHLCV availability (price history), mirroring the fundamentals markers above:
    ohlcv_status: str | None = None  # pending / available / unavailable
    ohlcv_as_of: datetime.datetime | None = None
    ohlcv_earliest: datetime.date | None = None  # confirmed earliest-available date (backward-gap floor)
    ohlcv_empty_streak: int | None = None  # consecutive empty fetches; 2 → unavailable


class StockQuarterly(SQLModel, table=True):
    """Quarterly P&L per symbol, scraped from screener.in's Quarterly Results table."""

    __tablename__ = "stock_quarterly"
    __table_args__ = TABLE_ARGS

    symbol: str = Field(primary_key=True, index=True)
    period_end: datetime.date = Field(primary_key=True)  # quarter-end (e.g. 2026-03-31)
    period_label: str | None = None  # screener label, e.g. "Mar 2026"
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
    # screener.in top ratios
    market_cap: float | None = None
    current_price: float | None = None
    stock_pe: float | None = None
    book_value: float | None = None
    dividend_yield: float | None = None
    roce: float | None = None
    roe: float | None = None
    face_value: float | None = None
    # latest reported quarter
    last_quarter_label: str | None = None
    sales_latest_q: float | None = None
    net_profit_latest_q: float | None = None
    opm_latest_q: float | None = None
    eps_latest_q: float | None = None
    # derived growth (from quarterly history)
    yoy_sales_growth: float | None = None
    yoy_profit_growth: float | None = None
    qoq_sales_growth: float | None = None
    qoq_profit_growth: float | None = None
    # ownership
    promoter_holding: float | None = None
    promoter_holding_change_1y: float | None = None
    # price-derived (CAPM vs Nifty 50, from OHLCV)
    return_1y: float | None = None
    vol_1y: float | None = None
    beta_1y: float | None = None
    alpha_1y: float | None = None
    r2_1y: float | None = None
    computed_at: datetime.datetime | None = None
