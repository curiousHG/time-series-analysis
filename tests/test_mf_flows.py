"""Net-flow maths over average AUM and NAV."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from services.mf.flows import (
    aggregate_flows,
    canonical_category,
    compute_scheme_flows,
    fund_family,
    trailing_flow_summary,
)

Q1, Q2, Q3 = date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30)


def _aum_nav() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "scheme_code": [1, 1, 1, 2, 2],
            "period_end": [Q1, Q2, Q3, Q2, Q3],
            "scheme_name": ["A - Direct Plan - Growth"] * 3 + ["A - Regular Plan - Growth"] * 2,
            "aum_lakhs": [10_000.0, 12_000.0, 11_000.0, 5_000.0, 5_000.0],
            "category": ["Flexi"] * 5,
            "fund_house": ["X"] * 5,
            "avg_nav": [10.0, 11.0, None, 20.0, 18.0],
        }
    )


def test_flow_is_aum_growth_not_explained_by_nav_growth():
    flows = compute_scheme_flows(_aum_nav()).filter(pl.col("scheme_code") == 1)
    first, second, third = flows.rows(named=True)
    assert first["flow_cr"] is None
    assert second["flow_cr"] == pytest.approx((12_000 - 10_000 * 1.1) / 100)
    assert second["flow_pct"] == pytest.approx(0.1)
    assert second["aum_cr"] == pytest.approx(120.0)
    assert third["flow_cr"] is None


def test_falling_nav_with_flat_aum_is_an_inflow():
    flows = compute_scheme_flows(_aum_nav()).filter(pl.col("scheme_code") == 2)
    assert flows["flow_cr"].to_list()[1] == pytest.approx((5_000 - 5_000 * 0.9) / 100)


def test_a_missing_quarter_breaks_the_chain_instead_of_spanning_it():
    gapped = pl.DataFrame(
        {
            "scheme_code": [1, 1, 1],
            "period_end": [date(2022, 12, 31), date(2024, 6, 30), date(2024, 9, 30)],
            "scheme_name": ["A"] * 3,
            "aum_lakhs": [100.0, 400.0, 420.0],
            "category": ["Flexi"] * 3,
            "fund_house": ["X"] * 3,
            "avg_nav": [10.0, 12.0, 12.0],
        }
    )
    flows = compute_scheme_flows(gapped)
    assert flows["prev_aum_lakhs"].to_list() == [None, None, 400.0]
    assert flows["flow_cr"].to_list()[:2] == [None, None]
    assert flows["flow_cr"].to_list()[2] == pytest.approx(0.2)


def test_aggregation_sums_plans_and_scales_by_priced_prior_aum():
    flows = compute_scheme_flows(_aum_nav())
    q3 = aggregate_flows(flows, "category").filter(pl.col("period_end") == Q3).row(0, named=True)
    assert q3["plans"] == 2
    assert q3["aum_cr"] == pytest.approx(160.0)
    assert q3["flow_cr"] == pytest.approx(5.0)
    assert q3["flow_pct"] == pytest.approx(5.0 / 50.0)


def test_trailing_summary_counts_inflow_periods_and_growth():
    flows = aggregate_flows(compute_scheme_flows(_aum_nav()), "category")
    row = trailing_flow_summary(flows, "category", periods=2).row(0, named=True)
    assert row["as_of"] == Q3
    assert row["aum_cr"] == pytest.approx(160.0)
    assert row["aum_cr_start"] == pytest.approx(100.0)
    assert row["inflow_periods"] == 2
    assert row["aum_growth"] == pytest.approx(0.6)


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "Parag Parikh Flexi Cap Fund"),
        ("Motilal Oswal Midcap Fund-Direct Plan-Growth Option", "Motilal Oswal Midcap Fund"),
        ("UTI Nifty 50 Index Fund - Growth Option- Direct", "UTI Nifty 50 Index Fund"),
        ("UTI Nifty 50 Index Fund - IDCW Option- Direct", "UTI Nifty 50 Index Fund"),
        ("Quant Small Cap Fund (Direct) - Growth", "Quant Small Cap Fund"),
        ("ICICI Prudential Growth Leaders Fund - Direct Plan", "ICICI Prudential Growth Leaders Fund"),
        (None, None),
    ],
)
def test_fund_family_strips_plan_and_option(name, family):
    assert fund_family(name) == family


@pytest.mark.parametrize(
    ("label", "group"),
    [
        ("Equity Scheme - Small Cap Fund", "Small Cap Fund"),
        ("Equity Schemes - Small Cap Fund", "Small Cap Fund"),
        ("Equity Scheme - Sectoral/ Thematic", "Sectoral/Thematic"),
        ("Equity Schemes - Thematic Fund", "Sectoral/Thematic"),
        ("Equity Schemes - ELSS- Tax Saver Fund", "ELSS"),
        ("Other Scheme - Index Funds", "Index Funds"),
        ("Index Funds - Equity Funds", "Index Funds"),
        ("Index Funds - Debt Funds", "Index Funds (Debt)"),
        ("Income/Debt Oriented Schemes - Liquid Fund", "Liquid Fund"),
        ("Income/Debt Oriented Schemes - Sectoral Fund", "Sectoral Fund"),
        ("Other Scheme - Other  ETFs", "Other ETFs"),
        (None, None),
    ],
)
def test_canonical_category_merges_amc_label_variants(label, group):
    assert canonical_category(label) == group
