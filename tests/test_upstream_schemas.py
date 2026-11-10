"""Upstream payload models: shape errors, lenient Kuvera values, extra keys, metadata round-trip."""

from __future__ import annotations

import datetime
import time
from unittest.mock import patch

import httpx
import pytest

from data.exceptions import UpstreamFormatError
from data.fetchers import kuvera
from data.fetchers import mfapi as mf
from data.fetchers.schemas import FundMetadata, KuveraSchemeDetail, MfapiNav, MfapiSearchHit, validate_upstream


def test_mfapi_envelope_missing_data_is_an_upstream_format_error():
    with pytest.raises(UpstreamFormatError) as exc:
        validate_upstream("MFAPI", MfapiNav, {"meta": {}})
    assert exc.value.source == "MFAPI"
    assert "data" in exc.value.detail


def test_mfapi_row_missing_nav_is_an_upstream_format_error():
    with pytest.raises(UpstreamFormatError):
        validate_upstream("MFAPI", MfapiNav, {"data": [{"date": "01-01-2024"}]})


def test_mfapi_non_object_payload_is_an_upstream_format_error():
    with pytest.raises(UpstreamFormatError):
        validate_upstream("MFAPI", MfapiNav, [1, 2])


def test_mfapi_empty_data_still_raises_value_error():
    resp = httpx.Response(200, json={"data": []}, request=httpx.Request("GET", "http://x"))
    with patch.object(mf, "_get_mfapi", return_value=resp), pytest.raises(ValueError, match="No NAV data"):
        mf.fetch_nav_from_mfapi("1", "X")


def test_mfapi_extra_keys_are_ignored_and_rows_stay_plain_dicts():
    parsed = validate_upstream(
        "MFAPI", MfapiNav, {"meta": {"a": 1}, "data": [{"date": "01-01-2024", "nav": "1.5", "x": 1}]}
    )
    assert parsed.data == [{"date": "01-01-2024", "nav": "1.5"}]


def test_mfapi_search_hit_uses_aliases():
    hits = validate_upstream(
        "MFAPI search", list[MfapiSearchHit], [{"schemeCode": "119551", "schemeName": "A", "z": 1}]
    )
    assert hits[0].scheme_code == 119551 and hits[0].scheme_name == "A"


def test_mfapi_validation_of_5000_rows_is_fast():
    payload = {"data": [{"date": "01-01-2024", "nav": "10.123"} for _ in range(5000)]}
    validate_upstream("MFAPI", MfapiNav, payload)
    start = time.perf_counter()
    validate_upstream("MFAPI", MfapiNav, payload)
    assert time.perf_counter() - start < 0.01


@pytest.mark.parametrize(("raw", "expected"), [("", None), ("abc", None), ("12.5", 12.5), (12, 12.0), (None, None)])
def test_kuvera_numbers_are_lenient(raw, expected):
    assert KuveraSchemeDetail.model_validate({"aum": raw}).aum == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2013-01-01", datetime.date(2013, 1, 1)),
        ("2013-01-01T00:00:00", datetime.date(2013, 1, 1)),
        ("", None),
        ("junk", None),
    ],
)
def test_kuvera_dates_are_lenient(raw, expected):
    assert KuveraSchemeDetail.model_validate({"start_date": raw}).start_date == expected


def test_kuvera_detail_ignores_extra_keys_and_reads_isin_alias():
    d = KuveraSchemeDetail.model_validate({"ISIN": "INF1", "brand_new_field": [1]})
    assert d.isin == "INF1"


def test_kuvera_metadata_maps_detail_to_fund_metadata():
    detail = [
        {
            "ISIN": "INF1",
            "aum": "1000",
            "expense_ratio": "0.5",
            "expense_ratio_date": "2024-03-31",
            "start_date": "2013-01-01",
            "fund_category": "Flexi Cap",
            "fund_type": "Equity",
            "maturity_type": "Open Ended",
            "lump_min": 5000,
            "lump_min_additional": "",
            "crisil_rating": "Very High Risk",
            "slug": "s",
            "unknown": 1,
        }
    ]

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return detail

    with (
        patch.object(kuvera, "_candidate_codes", return_value=["C"]),
        patch.object(kuvera.httpx, "get", return_value=_Resp()),
    ):
        out = kuvera.fetch_fund_metadata_kuvera("F", ("INF1",))
    assert out is not None
    assert out.model_dump(exclude_unset=True) == {
        "scheme_name": "F",
        "aum_crores": 100.0,
        "aum_as_of": None,
        "expense_ratio": 0.5,
        "expense_ratio_as_of": datetime.date(2024, 3, 31),
        "benchmark": None,
        "launch_date": datetime.date(2013, 1, 1),
        "category": "Flexi Cap",
        "asset_class": "Equity",
        "status": "Open Ended",
        "min_investment": 5000.0,
        "min_topup": None,
        "turnover_ratio": None,
        "exit_load": None,
        "investment_objective": None,
        "risk_level": "Very High",
        "fund_manager": None,
        "fund_house": None,
        "source_url": "https://kuvera.in/mutual-funds/scheme/s",
    }


def test_fund_metadata_requires_scheme_name_and_is_frozen():
    with pytest.raises(ValueError):
        FundMetadata()
    meta = FundMetadata(scheme_name="X")
    with pytest.raises(ValueError):
        meta.aum_crores = 1.0


def test_unset_metadata_fields_are_not_written():
    meta = FundMetadata(scheme_name="X", aum_crores=None)
    assert meta.model_dump(exclude_unset=True) == {"scheme_name": "X", "aum_crores": None}
