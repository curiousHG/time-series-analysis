"""SQLModel ORM models — each model is also a Pydantic BaseModel."""

import datetime
from sqlmodel import SQLModel, Field

TABLE_ARGS = {"extend_existing": True}


class MfNav(SQLModel, table=True):
    __tablename__ = "mf_nav"
    __table_args__ = TABLE_ARGS

    date: datetime.date = Field(primary_key=True)
    scheme_name: str = Field(primary_key=True)
    nav: float


class MfHolding(SQLModel, table=True):
    __tablename__ = "mf_holdings"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    scheme_code: str | None = None
    scheme_name: str | None = None
    scheme_slug: str = Field(index=True)
    scheme_common: str | None = None
    portfolio_date: datetime.date | None = None
    instrument_name: str | None = None
    isin: str | None = None
    issuer_name: str | None = None
    asset_class: str | None = None
    asset_sub_class: str | None = None
    asset_type: str | None = None
    weight: float | None = None
    value: float | None = None
    quantity: float | None = None
    industry: str | None = None
    market_cap: str | None = None
    credit_rating: str | None = None
    credit_rating_eq: str | None = None


class MfSectorAllocation(SQLModel, table=True):
    __tablename__ = "mf_sector_allocation"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    scheme_code: str | None = None
    scheme_name: str | None = None
    scheme_slug: str = Field(index=True)
    portfolio_date: datetime.date | None = None
    sector: str | None = None
    weight: float | None = None


class MfAssetAllocation(SQLModel, table=True):
    __tablename__ = "mf_asset_allocation"
    __table_args__ = TABLE_ARGS

    id: int | None = Field(default=None, primary_key=True)
    scheme_code: str | None = None
    scheme_name: str | None = None
    scheme_slug: str = Field(index=True)
    portfolio_date: datetime.date | None = None
    asset_class: str | None = None
    weight: float | None = None


class MfRegistry(SQLModel, table=True):
    __tablename__ = "mf_registry"
    __table_args__ = TABLE_ARGS

    scheme_name: str = Field(primary_key=True)
    scheme_slug: str
    source: str = "advisorkhoj"


class SchemeCodeMap(SQLModel, table=True):
    __tablename__ = "scheme_code_map"
    __table_args__ = TABLE_ARGS

    scheme_name: str = Field(primary_key=True)
    scheme_code: str


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


class FundMapping(SQLModel, table=True):
    __tablename__ = "fund_mapping"
    __table_args__ = TABLE_ARGS

    trade_symbol: str = Field(primary_key=True)
    mapped_nav_fund: str | None = None


class MfTradebook(SQLModel, table=True):
    __tablename__ = "mf_tradebook"
    __table_args__ = TABLE_ARGS

    trade_id: str = Field(primary_key=True)
    symbol: str
    isin: str
    trade_date: datetime.date
    exchange: str | None = None
    segment: str | None = None
    series: str | None = None
    trade_type: str
    auction: str | None = None
    quantity: float
    price: float
    order_id: str | None = None
    order_execution_time: str | None = None
