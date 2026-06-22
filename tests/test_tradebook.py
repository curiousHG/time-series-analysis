"""Unit tests for mutual_funds.tradebook pure transforms (no DB)."""

from datetime import date


def _raw(rows):
    """Build a raw tradebook frame matching load_tradebook_from_db's output contract."""
    import polars as pl

    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.Utf8,
            "isin": pl.Utf8,
            "trade_date": pl.Date,
            "trade_type": pl.Utf8,
            "quantity": pl.Float64,
            "price": pl.Float64,
            "scheme_code": pl.Int64,
            "schemeName": pl.Utf8,
        },
        orient="row",
    )


def test_normalize_transactions_signs_qty_and_computes_value():
    from mutual_funds.tradebook import normalize_transactions

    df = _raw(
        [
            ("SYM", "IN1", date(2024, 1, 1), "buy", 10.0, 5.0, 1, "Alpha"),
            ("SYM", "IN1", date(2024, 1, 2), "sell", 4.0, 6.0, 1, "Alpha"),
        ]
    )
    out = normalize_transactions(df).sort("trade_date")

    assert out["signed_qty"].to_list() == [10.0, -4.0]
    assert out["trade_value"].to_list() == [50.0, 24.0]


def test_normalize_transactions_trade_type_is_case_insensitive():
    from mutual_funds.tradebook import normalize_transactions

    df = _raw(
        [
            ("SYM", "IN1", date(2024, 1, 1), "BUY", 10.0, 5.0, 1, "Alpha"),
            ("SYM", "IN1", date(2024, 1, 2), "Sell", 4.0, 6.0, 1, "Alpha"),
        ]
    )
    out = normalize_transactions(df).sort("trade_date")

    assert out["signed_qty"].to_list() == [10.0, -4.0]


def test_normalize_transactions_projects_canonical_columns():
    import polars as pl

    from mutual_funds.tradebook import normalize_transactions

    df = _raw([("SYM", "IN1", date(2024, 1, 1), "buy", 10.0, 5.0, 1, "Alpha")])
    out = normalize_transactions(df)

    assert out.columns == [
        "symbol",
        "isin",
        "trade_date",
        "scheme_code",
        "schemeName",
        "signed_qty",
        "price",
        "trade_value",
    ]
    assert out.schema["trade_date"] == pl.Date


def _norm(rows):
    """A minimal normalized frame: schemeName, trade_date, signed_qty."""
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


def test_compute_daily_units_cumulates_per_scheme():
    from mutual_funds.tradebook import compute_daily_units

    df = _norm(
        [
            ("Alpha", date(2024, 1, 1), 10.0),
            ("Alpha", date(2024, 1, 2), 5.0),
            ("Alpha", date(2024, 1, 3), -3.0),
        ]
    )
    out = compute_daily_units(df).sort("date")

    assert out.columns == ["schemeName", "date", "units"]
    assert out["units"].to_list() == [10.0, 15.0, 12.0]


def test_compute_daily_units_keeps_schemes_independent():
    from mutual_funds.tradebook import compute_daily_units

    df = _norm(
        [
            ("Alpha", date(2024, 1, 1), 10.0),
            ("Beta", date(2024, 1, 1), 100.0),
            ("Alpha", date(2024, 1, 2), 5.0),
            ("Beta", date(2024, 1, 2), 50.0),
        ]
    )
    out = compute_daily_units(df)

    alpha = out.filter(out["schemeName"] == "Alpha").sort("date")["units"].to_list()
    beta = out.filter(out["schemeName"] == "Beta").sort("date")["units"].to_list()
    assert alpha == [10.0, 15.0]
    assert beta == [100.0, 150.0]


def test_compute_daily_units_aggregates_same_day_trades():
    from mutual_funds.tradebook import compute_daily_units

    df = _norm(
        [
            ("Alpha", date(2024, 1, 1), 10.0),
            ("Alpha", date(2024, 1, 1), 4.0),
            ("Alpha", date(2024, 1, 2), -2.0),
        ]
    )
    out = compute_daily_units(df).sort("date")

    # Same-day trades collapse to one row of 14, then cumulate to 12.
    assert out["date"].to_list() == [date(2024, 1, 1), date(2024, 1, 2)]
    assert out["units"].to_list() == [14.0, 12.0]
