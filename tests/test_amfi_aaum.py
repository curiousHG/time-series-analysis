"""AMFI average-AUM fetcher parsing and the DB-first period sync."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from data.exceptions import UpstreamFormatError
from data.fetchers import amfi_aaum
from data.fetchers.amfi_aaum import AaumPeriod, aaum_frame, parse_period_end
from data.fetchers.schemas import AmfiAaumData, validate_upstream
from data.repositories import aaum as repo

if TYPE_CHECKING:
    import polars as pl


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("July - September 2026", date(2026, 9, 30)),
        ("January - March 2024", date(2024, 3, 31)),
        ("October - December 2023", date(2023, 12, 31)),
        ("February 2008", date(2008, 2, 29)),
        ("March 2007 ", date(2007, 3, 31)),
    ],
)
def test_period_label_maps_to_last_day_of_final_month(label, expected):
    assert parse_period_end(label) == expected


def test_unrecognised_period_label_is_an_upstream_format_error():
    with pytest.raises(UpstreamFormatError):
        parse_period_end("Q3 FY27")


def _payload(*schemes: tuple[int, float, float]) -> dict:
    return {
        "data": [
            {
                "Mfname": "Some MF",
                "SchemeCat_Desc": "Equity Scheme - Flexi Cap Fund",
                "schemes": [
                    {
                        "SchemeNAVName": f" Scheme {code} - Direct Plan - Growth ",
                        "AMFI_Code": code,
                        "AverageAumForTheMonth": {
                            "ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas": excl,
                            "FundOfFundsDomestic": fof,
                        },
                    }
                    for code, excl, fof in schemes
                ],
                "totalAUM": {},
            }
        ]
    }


def test_payload_flattens_to_one_row_per_scheme_and_keeps_largest_duplicate():
    payload = validate_upstream("AMFI AAUM", AmfiAaumData, _payload((1, 500.0, 0), (2, 0, 75.5), (1, 20.0, 0)))
    frame = aaum_frame(payload, date(2026, 9, 30))
    assert frame.columns == list(amfi_aaum.AAUM_SCHEMA)
    assert frame["scheme_code"].to_list() == [1, 2]
    assert frame["aaum_lakhs"].to_list() == [500.0, 0.0]
    assert frame["fof_domestic_lakhs"].to_list() == [0.0, 75.5]
    assert frame["scheme_name"][0] == "Scheme 1 - Direct Plan - Growth"
    assert frame["category"][0] == "Equity Scheme - Flexi Cap Fund"


def test_payload_without_scheme_code_is_an_upstream_format_error():
    bad = _payload((1, 1.0, 0))
    del bad["data"][0]["schemes"][0]["AMFI_Code"]
    with pytest.raises(UpstreamFormatError):
        validate_upstream("AMFI AAUM", AmfiAaumData, bad)


def test_sync_fetches_only_periods_not_already_stored(monkeypatch):
    stored = date(2026, 6, 30)
    periods = [
        AaumPeriod(1, 2, "April - June 2026", stored),
        AaumPeriod(1, 1, "July - September 2026", date(2026, 9, 30)),
    ]
    fetched: list[str] = []
    saved: list[pl.DataFrame] = []

    def fake_fetch(period: AaumPeriod) -> pl.DataFrame:
        fetched.append(period.label)
        return aaum_frame(validate_upstream("AMFI AAUM", AmfiAaumData, _payload((9, 1.0, 0))), period.period_end)

    monkeypatch.setattr(repo, "get_aaum_period_ends", lambda: {stored})
    monkeypatch.setattr(repo, "fetch_aaum_periods", lambda since_year: periods)
    monkeypatch.setattr(repo, "fetch_aaum_period", fake_fetch)
    monkeypatch.setattr(repo, "save_aaum", lambda frame: saved.append(frame) or frame.height)

    assert repo.sync_aaum(2016) == 1
    assert fetched == ["July - September 2026"]
    assert saved[0]["period_end"].to_list() == [date(2026, 9, 30)]
