from datetime import date

import polars as pl

from mutual_funds.tradebook import daily_net_invested, signed_flows


def _mapped() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "schemeName": ["A", "A", "B"],
            "trade_date": [date(2024, 1, 1), date(2024, 2, 1), date(2024, 1, 1)],
            "signed_qty": [10.0, -4.0, 5.0],
            "trade_value": [1000.0, 500.0, 250.0],
        }
    )


def test_signed_flows_negate_sells_and_filter_by_scheme():
    all_flows = signed_flows(_mapped())
    assert all_flows.columns == ["date", "amount"]
    assert all_flows["amount"].to_list() == [1000.0, -500.0, 250.0]
    assert signed_flows(_mapped(), "A")["amount"].to_list() == [1000.0, -500.0]


def test_daily_net_invested_nets_per_day_and_accumulates():
    out = daily_net_invested(_mapped())
    assert out["date"].to_list() == [date(2024, 1, 1), date(2024, 2, 1)]
    assert out["invested"].to_list() == [1250.0, -500.0]
    assert out["cum_invested"].to_list() == [1250.0, 750.0]
