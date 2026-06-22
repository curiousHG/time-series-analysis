from datetime import datetime


def test_prepare_amfi_sync_rows_sets_db_added_at_only_for_new_codes():
    from data.repositories.amfi import _prepare_sync_row

    synced_at = datetime(2026, 6, 13, 10, 30, 0)
    scheme = {
        "scheme_code": 123,
        "scheme_name": "New Fund - Growth",
        "fund_house": "Example AMC",
        "category": "Equity",
        "nav": 10.0,
    }

    row = _prepare_sync_row(
        scheme,
        existing_codes=set(),
        fund_house_id=1,
        category_id=2,
        synced_at=synced_at,
    )

    assert row["db_added_at"] == synced_at
    assert row["fund_house_id"] == 1
    assert row["category_id"] == 2
    assert "fund_house" not in row
    assert "category" not in row


def test_prepare_amfi_sync_rows_preserves_existing_added_at():
    from data.repositories.amfi import _prepare_sync_row

    synced_at = datetime(2026, 6, 13, 10, 30, 0)
    scheme = {
        "scheme_code": 123,
        "scheme_name": "Existing Fund - Growth",
        "fund_house": "Example AMC",
        "category": "Equity",
    }

    row = _prepare_sync_row(
        scheme,
        existing_codes={123},
        fund_house_id=1,
        category_id=2,
        synced_at=synced_at,
    )

    assert "db_added_at" not in row
