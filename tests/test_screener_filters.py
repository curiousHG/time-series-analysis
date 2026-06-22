import polars as pl

from services.screener_service import apply_filters


def test_apply_filters_treats_search_text_as_literal_text():
    df = pl.DataFrame(
        {
            "scheme_name": ["Alpha (Growth) Fund", "Beta Growth Fund"],
            "fund_house": ["AMC", "AMC"],
            "category": ["Equity", "Equity"],
            "plan": ["Direct", "Direct"],
            "option": ["Growth", "Growth"],
            "aum_crores": [100.0, 100.0],
            "expense_ratio": [1.0, 1.0],
            "nav_status": ["available", "available"],
            "cagr_1y": [0.1, 0.1],
            "sharpe_1y": [1.0, 1.0],
            "max_dd_1y": [-0.1, -0.1],
        }
    )

    result = apply_filters(
        df,
        name_query="(",
        amcs=[],
        cats=[],
        plans=[],
        options=[],
        aum_min=0,
        ter_max=5.0,
        only_tracked=False,
        has_nav=False,
    )

    assert result["scheme_name"].to_list() == ["Alpha (Growth) Fund"]
