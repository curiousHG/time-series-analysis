"""AMFI NAVAll.txt parsing: column layout, name composition, category canonicalisation, and the
format-drift guards.

The regression these cover: AMFI split the plan/option suffix out of `Scheme Name` into new `Plan`
and `Option` columns, shifting NAV and date one place right. The old positional parser kept
"succeeding" while nulling every NAV and collapsing 14K scheme names onto 3.7K duplicates.
"""

from __future__ import annotations

import datetime

import pytest

from data.exceptions import UpstreamFormatError
from data.fetchers.mutual_fund import (
    _classify_amfi_header,
    compose_scheme_name,
    parse_amfi_master,
)

_NEW_HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date"
)
_OLD_HEADER = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date"


def _feed(header: str, rows: list[str], *, category: str = "Open Ended Schemes(Equity Scheme - Large Cap Fund)") -> str:
    """A NAVAll.txt payload padded past the parser's minimum-scheme floor."""
    filler = [
        f"{900000 + i};INF{i:09d};-;Filler Fund {i};Direct Plan;Growth Option;10.0;03-Sep-2026"
        if header == _NEW_HEADER
        else f"{900000 + i};INF{i:09d};-;Filler Fund {i} - Direct - Growth;10.0;03-Sep-2026"
        for i in range(1200)
    ]
    return "\n".join([header, "", category, "", "Axis Mutual Fund", "", *rows, *filler])


def test_parses_current_eight_column_layout():
    payload = _feed(
        _NEW_HEADER,
        ["135762;INF846K01WO1;-;Axis Children's Fund;Direct Plan;Growth Option;30.3234;03-Sep-2026"],
    )
    row = next(r for r in parse_amfi_master(payload) if r["scheme_code"] == 135762)

    assert row["scheme_name"] == "Axis Children's Fund - Direct Plan - Growth Option"
    assert row["plan"] == "Direct Plan"
    assert row["option"] == "Growth Option"
    assert row["nav"] == pytest.approx(30.3234)
    assert row["nav_date"] == datetime.date(2026, 9, 3)


def test_still_parses_legacy_six_column_layout():
    payload = _feed(
        _OLD_HEADER,
        ["120503;INF846K01EW2;-;Axis ELSS Tax Saver Fund - Direct Plan - Growth;111.56;02-Sep-2026"],
    )
    row = next(r for r in parse_amfi_master(payload) if r["scheme_code"] == 120503)

    assert row["scheme_name"] == "Axis ELSS Tax Saver Fund - Direct Plan - Growth"
    assert row["plan"] is None and row["option"] is None
    assert row["nav"] == pytest.approx(111.56)
    assert row["nav_date"] == datetime.date(2026, 9, 2)


def test_blank_plan_option_keeps_the_bare_name():
    """Closed-ended FMPs carry empty Plan/Option columns — the name must not gain stray dashes."""
    payload = _feed(_NEW_HEADER, ["145670;INF204KB1P66;-;Nippon India Interval Fund V - Series 2;;;10.85;19-Dec-2019"])
    row = next(r for r in parse_amfi_master(payload) if r["scheme_code"] == 145670)

    assert row["scheme_name"] == "Nippon India Interval Fund V - Series 2"
    assert row["plan"] is None and row["option"] is None


def test_columns_are_read_from_the_header_not_by_position():
    """A reordered feed is followed by name, so a future column move doesn't silently shift NAV."""
    header = (
        "Scheme Code;Scheme Name;Plan;Option;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date"
    )
    payload = _feed(
        header,
        ["135762;Axis Children's Fund;Direct Plan;Growth Option;INF846K01WO1;-;30.3234;03-Sep-2026"],
    )
    # The filler rows use the standard order, so only assert on the reordered row.
    row = next(r for r in parse_amfi_master(payload) if r["scheme_code"] == 135762)
    assert row["isin_growth"] == "INF846K01WO1"
    assert row["nav"] == pytest.approx(30.3234)


@pytest.mark.parametrize(
    ("header_line", "expected_class", "expected_sub"),
    [
        ("Open Ended Schemes(Equity Scheme - Large Cap Fund)", "Equity", "Large Cap Fund"),
        # The plural spelling used to yield "Equity s" via a naive .replace("Scheme", "").
        ("Open Ended Schemes(Equity Schemes - Large Cap Fund)", "Equity", "Large Cap Fund"),
        ("Close Ended Schemes(Income/Debt Oriented Schemes - Fixed Term Plan)", "Debt", "Fixed Term Plan"),
        ("Open Ended Schemes(Exchange Traded Funds (ETFs) - Gold ETF)", "ETF", "Gold ETF"),
        (
            "Open Ended Schemes(Fund of Funds Scheme (Domestic) - Fund of Funds Scheme (Domestic))",
            "Fund of Funds",
            "Fund of Funds (Domestic)",
        ),
        ("Open Ended Schemes(Solution Oriented Schemes ** - Retirement Fund)", "Solution Oriented", "Retirement Fund"),
        # SEBI files index funds / ETFs / FoFs under "Other Scheme", naming the real class in the sub.
        ("Open Ended Schemes(Other Scheme - Index Funds)", "Index", "Index Funds"),
        ("Open Ended Schemes(Other Scheme - FoF Overseas)", "Fund of Funds", "FoF Overseas"),
        ("Open Ended Schemes(Other Scheme - Other  ETFs)", "ETF", "Other ETFs"),
    ],
)
def test_asset_classes_collapse_onto_the_canonical_vocabulary(header_line, expected_class, expected_sub):
    asset_class, sub_category, fund_house = _classify_amfi_header(header_line)
    assert (asset_class, sub_category) == (expected_class, expected_sub)
    assert fund_house is None


def test_amc_lines_are_not_mistaken_for_categories():
    assert _classify_amfi_header("IL&FS Mutual Fund (IDF)") == (None, None, "IL&FS Mutual Fund (IDF)")


def test_compose_scheme_name_skips_missing_parts():
    assert compose_scheme_name("A Fund", "Direct Plan", "Growth Option") == "A Fund - Direct Plan - Growth Option"
    assert compose_scheme_name("A Fund", None, None) == "A Fund"
    assert compose_scheme_name("A Fund", "Direct Plan", None) == "A Fund - Direct Plan"


# ---- Format-drift guards -----------------------------------------------------------------


def test_raises_when_the_nav_column_moves():
    """The exact 2025 failure: NAV shifted out from under a positional read."""
    rows = [f"{900000 + i};INF{i:09d};-;Fund {i};Direct Plan;Growth Option;N.A.;03-Sep-2026" for i in range(1200)]
    with pytest.raises(UpstreamFormatError, match="nav"):
        parse_amfi_master("\n".join([_NEW_HEADER, *rows]))


def test_raises_when_the_feed_is_truncated():
    payload = "\n".join([_NEW_HEADER, "100001;INF000000001;-;Only Fund;Direct Plan;Growth Option;10.0;03-Sep-2026"])
    with pytest.raises(UpstreamFormatError, match="expected >="):
        parse_amfi_master(payload)


def test_raises_when_the_header_becomes_unrecognisable():
    rows = [f"{900000 + i};INF{i:09d};-;Fund {i};Direct Plan;Growth Option;10.0;03-Sep-2026" for i in range(1200)]
    with pytest.raises(UpstreamFormatError, match="no recognisable columns"):
        parse_amfi_master("\n".join(["Scheme Code Report v2", *rows]))


def test_missing_header_falls_back_to_positional_without_raising():
    """AMFI dropping the header row is survivable — the data still lines up."""
    rows = [f"{900000 + i};INF{i:09d};-;Fund {i};Direct Plan;Growth Option;10.0;03-Sep-2026" for i in range(1200)]
    parsed = parse_amfi_master("\n".join(rows))
    assert len(parsed) == 1200
    assert parsed[0]["nav"] == pytest.approx(10.0)
