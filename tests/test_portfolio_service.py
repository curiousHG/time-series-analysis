"""Unit tests for services.portfolio_service.build_portfolio_value_series (no DB)."""

from datetime import date


def _mapped(rows):
    """Minimal mapped-transactions frame consumed by compute_daily_units."""
    import polars as pl

    return pl.DataFrame(
        rows,
        schema={
            "schemeName": pl.Utf8,
            "trade_date": pl.Date,
            "signed_qty": pl.Float64,
        },
        orient="row",
    )


def _nav(rows):
    import polars as pl

    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "nav": pl.Float64, "schemeName": pl.Utf8},
        orient="row",
    )


def test_single_scheme_value_is_units_times_nav():
    from services.portfolio_service import build_portfolio_value_series

    mapped = _mapped([("Alpha", date(2024, 1, 1), 10.0)])
    nav = _nav(
        [
            (date(2024, 1, 1), 10.0, "Alpha"),
            (date(2024, 1, 2), 11.0, "Alpha"),
            (date(2024, 1, 3), 12.0, "Alpha"),
        ]
    )
    out = build_portfolio_value_series(mapped, nav)

    assert list(out.columns) == ["date", "portfolio_value"]
    assert out["portfolio_value"].tolist() == [100.0, 110.0, 120.0]


def test_two_schemes_with_unit_and_nav_forward_fill():
    from services.portfolio_service import build_portfolio_value_series

    mapped = _mapped(
        [
            ("Alpha", date(2024, 1, 1), 10.0),
            ("Beta", date(2024, 1, 2), 5.0),
        ]
    )
    # Alpha has no NAV on 01-02 (forward-filled from 10); Beta has no NAV on 01-01
    # (stays null → that row is dropped before summing).
    nav = _nav(
        [
            (date(2024, 1, 1), 10.0, "Alpha"),
            (date(2024, 1, 3), 12.0, "Alpha"),
            (date(2024, 1, 2), 20.0, "Beta"),
        ]
    )
    out = build_portfolio_value_series(mapped, nav)

    assert [d.date() for d in out["date"]] == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    # 01-01: Alpha 10*10 (Beta nav null → dropped)
    # 01-02: Alpha 10*10(ff) + Beta 5*20
    # 01-03: Alpha 10*12   + Beta 5*20(ff)
    assert out["portfolio_value"].tolist() == [100.0, 200.0, 220.0]


def test_empty_mapped_returns_none():
    from services.portfolio_service import build_portfolio_value_series

    mapped = _mapped([])
    nav = _nav([(date(2024, 1, 1), 10.0, "Alpha")])

    assert build_portfolio_value_series(mapped, nav) is None


def test_zero_value_rows_filtered_to_none():
    from services.portfolio_service import build_portfolio_value_series

    # Net units of 0 on the only date → value 0 → filtered out → None.
    mapped = _mapped(
        [
            ("Alpha", date(2024, 1, 1), 10.0),
            ("Alpha", date(2024, 1, 1), -10.0),
        ]
    )
    nav = _nav([(date(2024, 1, 1), 10.0, "Alpha")])

    assert build_portfolio_value_series(mapped, nav) is None
