"""NAV fetch/save/load must be keyed by scheme_code: AMFI publishes variants that share one name,
and a name-keyed path once stored an IDCW series under the Growth code."""

from datetime import date

import polars as pl


def _frame(name: str, nav: float) -> pl.DataFrame:
    return pl.DataFrame({"date": [date(2026, 9, 7)], "nav": [nav], "schemeName": [name]})


def test_fetch_single_nav_uses_the_given_code_and_tags_rows_with_it(monkeypatch):
    from data.repositories import nav

    asked: list[str] = []

    def fake_mfapi(code, name):
        asked.append(code)
        return _frame(name, 120.2)

    monkeypatch.setattr(nav, "fetch_nav_from_mfapi", fake_mfapi)

    def refuse(name):
        raise AssertionError("must not resolve by name")

    monkeypatch.setattr(nav, "lookup_scheme_code_by_exact_name", refuse)
    monkeypatch.setattr(nav, "resolve_mfapi_code", refuse)

    df = nav.fetch_single_nav("Motilal Oswal Midcap Fund", scheme_code=127042)

    assert asked == ["127042"]
    assert df["scheme_code"].to_list() == [127042]


def test_save_nav_df_writes_under_the_row_code_not_the_name(monkeypatch):
    from data.repositories import nav

    written: list[dict] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def exec(self, stmt):
            written.append(stmt.compile().params)

        def commit(self):
            pass

    monkeypatch.setattr(nav, "get_session", lambda: FakeSession())
    monkeypatch.setattr(nav, "resolve_codes_with_synthetic", lambda names: {"Motilal Oswal Midcap Fund": 127039})

    df = _frame("Motilal Oswal Midcap Fund", 120.2).with_columns(pl.lit(127042).alias("scheme_code"))
    nav.save_nav_df(df)

    assert [p["scheme_code"] for p in written] == [127042]


def test_get_mapped_data_loads_nav_by_the_trades_codes(monkeypatch):
    from services import portfolio_service

    requested: list[list[int]] = []

    def fake_load(codes):
        requested.append(sorted(codes))
        return pl.DataFrame(
            {
                "date": [date(2026, 9, 7), date(2026, 9, 7)],
                "nav": [120.2, 49.0],
                "scheme_code": [127042, 127039],
            }
        )

    monkeypatch.setattr(portfolio_service, "load_nav_by_codes", fake_load)
    txn = pl.DataFrame(
        {
            "scheme_code": [127042, 127042],
            "schemeName": ["Motilal Oswal Midcap Fund - Growth (127042)"] * 2,
            "trade_date": [date(2026, 1, 1), date(2026, 2, 1)],
            "quantity": [10.0, 10.0],
        }
    )

    _, nav_df = portfolio_service.get_mapped_data(txn)

    assert requested == [[127042]]
    assert nav_df["scheme_code"].to_list() == [127042]
    assert nav_df["schemeName"].to_list() == ["Motilal Oswal Midcap Fund - Growth (127042)"]
    assert nav_df["nav"].to_list() == [120.2]
