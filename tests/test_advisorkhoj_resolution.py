"""AdvisorKhoj resolution after AMFI's 2025 name change.

AdvisorKhoj keys funds by AMFI's *old* scheme names and answers unknown ones with HTTP 200 +
its homepage. Before these guards, the metadata scraper stored a row of Nones and marked the
fund "available", and the holdings fetch fuzzy-matched onto other funds' portfolios.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from data.exceptions import SourceNotFound
from data.fetchers import mutual_fund as mf
from mutual_funds.advisorkhoj import base_name, base_variants, name_candidates, same_fund, short_option

# ---- pure helpers ------------------------------------------------------------------------


def test_base_name_strips_composed_suffix():
    assert (
        base_name("Axis Children's Fund - Direct Plan - Growth Option", "Direct Plan", "Growth Option")
        == "Axis Children's Fund"
    )
    assert base_name("Some FMP Series 5", None, None) == "Some FMP Series 5"
    assert base_name("X - Direct Plan", "Direct Plan", None) == "X"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Growth Option", "Growth"),
        ("GROWTH", "Growth"),
        ("IDCW Option", "IDCW"),
        ("IDCW-Re-investment", "IDCW Reinvestment"),
        ("Weekly IDCW", "Weekly IDCW"),
    ],
)
def test_short_option(raw, expected):
    assert short_option(raw) == expected


def test_name_candidates_lead_with_known_spellings_and_measured_best_form():
    cands = name_candidates(
        "HDFC Large & Mid Cap Fund",
        "Direct Plan",
        "Growth Option",
        known=["HDFC Large and Mid Cap Fund - Direct Plan - Growth"],
    )
    assert cands[0] == "HDFC Large and Mid Cap Fund - Direct Plan - Growth"
    assert cands[1] == "HDFC Large & Mid Cap Fund - Direct Plan - Growth"
    assert cands[2] == "HDFC Large & Mid Cap Fund - Direct - Growth"
    assert cands[-1] == "HDFC Large & Mid Cap Fund"
    assert len(cands) == len({c.lower() for c in cands})


def test_name_candidates_without_plan_option_is_just_the_base():
    assert name_candidates("Kotak FMP Series 220", None, None) == ["Kotak FMP Series 220"]


def test_rename_notes_are_dropped_as_a_second_base_spelling():
    assert base_variants("Sundaram Large Cap Fund (Formerly Known as Sundaram Blue Chip Fund)") == [
        "Sundaram Large Cap Fund (Formerly Known as Sundaram Blue Chip Fund)",
        "Sundaram Large Cap Fund",
    ]
    assert base_variants("ICICI Prudential Large Cap Fund (erstwhile Bluechip Fund)") == [
        "ICICI Prudential Large Cap Fund (erstwhile Bluechip Fund)",
        "ICICI Prudential Large Cap Fund",
    ]
    assert base_variants("Plain Fund") == ["Plain Fund"]
    cands = name_candidates(
        "Sundaram Large Cap Fund (Formerly Known as Sundaram Blue Chip Fund)", "Regular Plan", "Growth"
    )
    assert "Sundaram Large Cap Fund - Regular Plan - Growth" in cands


def test_same_fund_ignores_punctuation_and_case():
    assert same_fund("Axis ELSS- Tax Saver Fund", "Axis ELSS Tax Saver Fund")
    assert not same_fund("Axis ELSS Tax Saver Fund", "Axis Bluechip Fund")


@pytest.mark.parametrize(
    ("returned", "ours"),
    [
        ("JM Liquid Fund - Bonus Option", "JM Liquid Fund"),
        ("Franklin India Liquid Fund- Institution", "Franklin India Liquid Fund"),
        ("Franklin India Liquid Fund - Super Institutional", "Franklin India Liquid Fund"),
        ("ICICI Prudential Money Market Fund - Option", "ICICI Prudential Money Market Fund"),
    ],
)
def test_same_fund_ignores_trailing_share_class_qualifiers(returned, ours):
    assert same_fund(returned, ours)


def test_same_fund_keeps_qualifier_words_inside_the_fund_name():
    assert not same_fund("Nippon India Growth Fund", "Nippon India Fund")
    assert not same_fund("HDFC Retail Opportunities Fund", "HDFC Opportunities Fund")
    assert not same_fund("Franklin India Liquid Fund", "Franklin India Bond Fund")


# ---- fetcher guards ----------------------------------------------------------------------


class _Resp:
    def __init__(self, text: str = "", json_value=None, status: int = 200):
        self.text = text
        self._json = json_value
        self.status_code = status

    def raise_for_status(self):
        return None

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def test_overview_scrape_rejects_the_homepage():
    homepage = "<html><title>Best Mutual Funds to Invest</title><body>nothing</body></html>"
    with patch.object(mf.httpx, "get", return_value=_Resp(text=homepage)), pytest.raises(SourceNotFound):
        mf.fetch_fund_overview_html("Nonexistent Fund - Direct Plan - Growth Option")


def test_overview_scrape_accepts_a_fund_page():
    page = "<html><table class='sch_over_table'><td>Launch Date: 24-05-2013</td></table></html>"
    with patch.object(mf.httpx, "get", return_value=_Resp(text=page)):
        assert "sch_over_table" in mf.fetch_fund_overview_html("Parag Parikh Flexi Cap Fund - Direct Plan - Growth")


def test_portfolio_post_raises_not_found_on_html_answer():
    with (
        patch.object(mf.httpx, "post", return_value=_Resp(text="<html>homepage</html>")),
        pytest.raises(SourceNotFound),
    ):
        mf.fetch_portfolio_by_slug("no-such-fund-direct-plan-growth-option")


# ---- resolver verification ---------------------------------------------------------------


def _payload(code: str, common: str) -> dict:
    return {
        "schemePortfolioAnalysisResponse": {
            "schemePortfolioList": [
                {"scheme_code": code, "scheme_name": f"{common} - Direct Plan - Growth", "scheme_amfi_common": common}
            ]
        }
    }


def test_resolver_rejects_a_different_fund_and_keeps_trying(monkeypatch):
    from data.repositories import holdings as repo

    class _Scheme:
        scheme_name, plan, option = "Alpha Fund - Direct Plan - Growth Option", "Direct Plan", "Growth Option"

    class _Reg:
        advisorkhoj_slug = None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, model, key):
            if model is repo.AmfiScheme and key == 1:
                return _Scheme()
            if model is repo.MfRegistry and key == 1:
                return _Reg()
            return None

        def add(self, *_):
            pass

        def commit(self):
            pass

    calls: list[str] = []

    def fake_post(slug):
        calls.append(slug)
        if slug == "alpha-fund-direct-plan-growth":
            return _payload("999", "Beta Fund")  # AdvisorKhoj fuzzy-matched onto another fund
        if slug == "alpha-fund-direct-growth":
            return _payload("1", "Alpha Fund")
        raise SourceNotFound("AdvisorKhoj portfolio", slug)

    stored: list[tuple[int, str]] = []
    monkeypatch.setattr(repo, "get_session", lambda: _Session())
    monkeypatch.setattr(repo, "fetch_portfolio_by_slug", fake_post)
    monkeypatch.setattr(repo, "_store_advisorkhoj_slug", lambda code, slug: stored.append((code, slug)))

    slug, resp = repo.resolve_and_fetch_portfolio(1)

    assert slug == "alpha-fund-direct-growth"
    assert calls[:2] == ["alpha-fund-direct-plan-growth", "alpha-fund-direct-growth"]
    assert stored == [(1, "alpha-fund-direct-growth")]
    assert resp["schemePortfolioAnalysisResponse"]["schemePortfolioList"][0]["scheme_code"] == "1"


def test_resolver_raises_when_nothing_verifies(monkeypatch):
    from data.repositories import holdings as repo

    class _Scheme:
        scheme_name, plan, option = "Gamma Fund - Regular Plan - IDCW", "Regular Plan", "IDCW"

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, model, key):
            return _Scheme() if model is repo.AmfiScheme and key == 2 else None

    monkeypatch.setattr(repo, "get_session", lambda: _Session())
    monkeypatch.setattr(repo, "fetch_portfolio_by_slug", lambda slug: _payload("777", "Delta Fund"))
    with pytest.raises(SourceNotFound):
        repo.resolve_and_fetch_portfolio(2)


# ---- metadata source tiers ---------------------------------------------------------------


def test_regular_plan_takes_scheme_level_fields_from_direct_sibling(monkeypatch):
    """Neither Kuvera (Direct-only) nor AdvisorKhoj knows the Regular plan: AUM, manager and
    riskometer come from the Direct twin; TER / launch / minimums stay empty."""
    from data.repositories import metadata as repo

    twin = {
        "scheme_name": "X Fund - Direct Plan - Growth",
        "aum_crores": 1234.5,
        "expense_ratio": 0.4,
        "launch_date": "2013-01-01",
        "min_investment": 5000.0,
        "fund_manager": "A. Manager",
        "risk_level": "Very High",
        "category": "Flexi Cap Fund",
        "asset_class": "Equity",
        "status": "Open Ended",
        "turnover_ratio": 0.3,
        "investment_objective": "Grow.",
        "source_url": "https://kuvera.in/mutual-funds/scheme/x-direct",
    }
    saved: list[dict] = []
    monkeypatch.setattr(
        repo,
        "get_scheme_details_by_name",
        lambda n: {"scheme_code": 2, "isin_growth": "INFREG", "isin_reinvestment": None},
    )
    monkeypatch.setattr(repo, "fetch_fund_metadata_kuvera", lambda name, isins: twin if "INFDIR" in isins else None)
    monkeypatch.setattr(
        repo, "_advisorkhoj_metadata", lambda name, code: (None, SourceNotFound("AdvisorKhoj overview", name))
    )
    monkeypatch.setattr(
        repo,
        "find_direct_sibling",
        lambda code: {
            "scheme_code": 1,
            "scheme_name": twin["scheme_name"],
            "isin_growth": "INFDIR",
            "isin_reinvestment": None,
        },
    )
    monkeypatch.setattr(repo, "save_metadata", saved.append)

    meta = repo.fetch_and_save("X Fund - Regular Plan - Growth")

    assert meta["scheme_name"] == "X Fund - Regular Plan - Growth"
    assert meta["aum_crores"] == 1234.5
    assert meta["fund_manager"] == "A. Manager" and meta["risk_level"] == "Very High"
    assert meta["expense_ratio"] is None and meta["launch_date"] is None and meta["min_investment"] is None
    assert saved == [meta]


def test_metadata_raises_when_every_tier_misses(monkeypatch):
    from data.repositories import metadata as repo

    monkeypatch.setattr(
        repo,
        "get_scheme_details_by_name",
        lambda n: {"scheme_code": 9, "isin_growth": "INF9", "isin_reinvestment": None},
    )
    monkeypatch.setattr(repo, "fetch_fund_metadata_kuvera", lambda name, isins: None)
    monkeypatch.setattr(
        repo, "_advisorkhoj_metadata", lambda name, code: (None, SourceNotFound("AdvisorKhoj overview", name))
    )
    monkeypatch.setattr(repo, "find_direct_sibling", lambda code: None)
    with pytest.raises(SourceNotFound):
        repo.fetch_and_save("Orphan Fund - Regular Plan - Growth")
