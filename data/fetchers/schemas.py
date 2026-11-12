"""Pydantic models for mutual-fund payloads, validated where they enter the app."""

from __future__ import annotations

from datetime import date
from functools import cache
from typing import Annotated, Any, NotRequired, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, ValidationError
from typing_extensions import TypedDict

from data.exceptions import UpstreamFormatError

_T = TypeVar("_T")


@cache
def _adapter(tp: Any) -> TypeAdapter:
    return TypeAdapter(tp)


def validate_upstream(source: str, tp: type[_T], payload: object) -> _T:
    """Validate `payload` against `tp`; a `ValidationError` surfaces as `UpstreamFormatError(source, ...)`."""
    try:
        return _adapter(tp).validate_python(payload)
    except ValidationError as e:
        first = e.errors(include_url=False, include_input=False)[0]
        where = ".".join(str(p) for p in first["loc"]) or "payload"
        raise UpstreamFormatError(source, f"{where}: {first['msg']}") from e


def _lenient_float(v: object) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _lenient_date(v: object) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


LenientFloat = Annotated[float | None, BeforeValidator(_lenient_float)]
LenientDate = Annotated[date | None, BeforeValidator(_lenient_date)]

_UPSTREAM = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class MfapiNavPoint(TypedDict):
    date: str
    nav: str


class MfapiNav(BaseModel):
    model_config = _UPSTREAM

    data: list[MfapiNavPoint]


class MfapiSearchHit(BaseModel):
    model_config = _UPSTREAM

    scheme_code: int = Field(alias="schemeCode")
    scheme_name: str = Field(alias="schemeName")


class AdvisorKhojNav(BaseModel):
    """AdvisorKhoj NAV answer: `nav_data` rows are `[epoch_ms, nav]`."""

    model_config = _UPSTREAM

    scheme: str
    launch_date: str
    nav_data: list[list[float | None]]


class KuveraListEntry(TypedDict, total=False):
    c: str | None
    n: str | None


KuveraSchemeList = dict[str, dict[str, dict[str, list[KuveraListEntry]]]]


class KuveraSchemeDetail(BaseModel):
    model_config = _UPSTREAM

    isin: str | None = Field(default=None, alias="ISIN")
    slug: str | None = None
    aum: LenientFloat = None
    expense_ratio: LenientFloat = None
    expense_ratio_date: LenientDate = None
    start_date: LenientDate = None
    fund_category: str | None = None
    fund_type: str | None = None
    maturity_type: str | None = None
    lump_min: LenientFloat = None
    lump_min_additional: LenientFloat = None
    portfolio_turnover: LenientFloat = None
    investment_objective: str | None = None
    crisil_rating: str | None = None
    fund_manager: str | None = None


class AmfiAaumYear(BaseModel):
    model_config = _UPSTREAM

    id: int
    financial_year: str


class AmfiAaumYears(BaseModel):
    model_config = _UPSTREAM

    data: list[AmfiAaumYear]


class AmfiAaumPeriod(BaseModel):
    model_config = _UPSTREAM

    id: int
    period: str


class AmfiAaumPeriodList(BaseModel):
    model_config = _UPSTREAM

    periods: list[AmfiAaumPeriod]


class AmfiAaumPeriods(BaseModel):
    model_config = _UPSTREAM

    data: AmfiAaumPeriodList


class AmfiAaumAmount(BaseModel):
    model_config = _UPSTREAM

    excl_fof_domestic: LenientFloat = Field(
        default=None, alias="ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas"
    )
    fof_domestic: LenientFloat = Field(default=None, alias="FundOfFundsDomestic")


class AmfiAaumScheme(BaseModel):
    model_config = _UPSTREAM

    scheme_name: str = Field(alias="SchemeNAVName")
    scheme_code: int = Field(alias="AMFI_Code")
    amount: AmfiAaumAmount = Field(alias="AverageAumForTheMonth")


class AmfiAaumGroup(BaseModel):
    model_config = _UPSTREAM

    fund_house: str | None = Field(default=None, alias="Mfname")
    category: str | None = Field(default=None, alias="SchemeCat_Desc")
    schemes: list[AmfiAaumScheme]


class AmfiAaumData(BaseModel):
    model_config = _UPSTREAM

    data: list[AmfiAaumGroup]


class NiftyTriRow(TypedDict):
    Date: str
    TotalReturnsIndex: str
    NTR_Value: NotRequired[str]


NiftyTriRows = list[NiftyTriRow]


class FundMetadata(BaseModel):
    """One scheme's metadata as saved to `mf_metadata`; unset fields are left out of the DB write."""

    model_config = _UPSTREAM

    scheme_name: str
    aum_crores: float | None = None
    aum_as_of: date | None = None
    expense_ratio: float | None = None
    expense_ratio_as_of: date | None = None
    benchmark: str | None = None
    launch_date: date | None = None
    category: str | None = None
    asset_class: str | None = None
    status: str | None = None
    min_investment: float | None = None
    min_topup: float | None = None
    turnover_ratio: float | None = None
    exit_load: str | None = None
    investment_objective: str | None = None
    risk_level: str | None = None
    fund_manager: str | None = None
    fund_house: str | None = None
    source_url: str | None = None
