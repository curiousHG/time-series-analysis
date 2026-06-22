from datetime import date

import polars as pl


def test_holdings_refresh_keeps_existing_rows_when_a_fetch_fails(monkeypatch):
    from mutual_funds.display import make_slug
    from services import sync_service

    deleted: list[list[str]] = []
    saved_holdings: list[pl.DataFrame] = []
    saved_sectors: list[pl.DataFrame] = []
    saved_assets: list[pl.DataFrame] = []

    def fake_fetch(slug: str):
        if slug == make_slug("Bad Fund"):
            raise RuntimeError("upstream failed")
        return (
            pl.DataFrame({"schemeSlug": [slug], "instrumentName": ["ABC"], "weight": [10.0]}),
            pl.DataFrame({"schemeSlug": [slug], "sector": ["Financials"], "weight": [10.0]}),
            pl.DataFrame({"schemeSlug": [slug], "assetClass": ["Equity"], "weight": [10.0]}),
        )

    monkeypatch.setattr(sync_service, "_fetch_normalize_holdings", fake_fetch)
    monkeypatch.setattr(sync_service, "delete_holdings_for_slugs", lambda slugs: deleted.append(list(slugs)))
    monkeypatch.setattr(sync_service, "save_holdings", lambda df: saved_holdings.append(df))
    monkeypatch.setattr(sync_service, "save_sectors", lambda df: saved_sectors.append(df))
    monkeypatch.setattr(sync_service, "save_assets", lambda df: saved_assets.append(df))

    result = sync_service.refresh_holdings_for_schemes(["Good Fund", "Bad Fund"])

    assert result.success_count == 1
    assert result.failures == [("Bad Fund", "upstream failed")]
    assert deleted == [[make_slug("Good Fund")]]
    assert len(saved_holdings) == 1
    assert len(saved_sectors) == 1
    assert len(saved_assets) == 1


def test_nav_refresh_deletes_only_successfully_fetched_schemes(monkeypatch):
    from data.repositories import nav

    deleted_params: list[dict] = []
    saved_frames: list[pl.DataFrame] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, stmt):
            deleted_params.append(stmt.compile().params)

        def commit(self):
            pass

    good_frame = pl.DataFrame(
        {
            "date": [date(2024, 1, 1)],
            "nav": [100.0],
            "schemeName": ["Good Fund"],
        }
    )

    codes = {"Good Fund": 101, "Bad Fund": 202}
    monkeypatch.setattr(nav, "_resolve_codes", lambda names: {name: codes[name] for name in names})
    monkeypatch.setattr(nav, "fetch_nav_parallel", lambda names: [good_frame])
    monkeypatch.setattr(nav, "save_nav_df", lambda df: saved_frames.append(df))
    monkeypatch.setattr(nav, "_recompute_metrics_for", lambda names: None)
    monkeypatch.setattr(nav, "load_nav_df", lambda names: pl.DataFrame())
    monkeypatch.setattr(nav, "get_session", lambda: FakeSession())

    nav.refresh_nav_data(["Good Fund", "Bad Fund"])

    assert len(saved_frames) == 1
    assert deleted_params == [{"scheme_code_1": [101]}]


def test_repository_holdings_refresh_deletes_only_successful_slugs(monkeypatch):
    from data.repositories import holdings

    deleted: list[list[str]] = []
    saved_holdings: list[pl.DataFrame] = []

    def fake_fetch(slug: str):
        if slug == "bad-fund":
            raise RuntimeError("upstream failed")
        return (
            pl.DataFrame({"schemeSlug": [slug], "instrumentName": ["ABC"], "weight": [10.0]}),
            pl.DataFrame({"schemeSlug": [slug], "sector": ["Financials"], "weight": [10.0]}),
            pl.DataFrame({"schemeSlug": [slug], "assetClass": ["Equity"], "weight": [10.0]}),
        )

    monkeypatch.setattr(holdings, "fetch_holdings_frames", fake_fetch)
    monkeypatch.setattr(holdings, "delete_holdings_for_slugs", lambda slugs: deleted.append(list(slugs)))
    monkeypatch.setattr(holdings, "save_holdings", lambda df: saved_holdings.append(df))
    monkeypatch.setattr(holdings, "save_sectors", lambda df: None)
    monkeypatch.setattr(holdings, "save_assets", lambda df: None)
    monkeypatch.setattr(holdings, "load_holdings", lambda slugs: pl.DataFrame())
    monkeypatch.setattr(holdings, "load_sectors", lambda slugs: pl.DataFrame())
    monkeypatch.setattr(holdings, "load_assets", lambda slugs: pl.DataFrame())

    holdings.refresh_holdings_data(["good-fund", "bad-fund"])

    assert deleted == [["good-fund"]]
    assert len(saved_holdings) == 1
