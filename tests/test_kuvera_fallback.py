"""Unit tests for the Kuvera fallback fetcher (pure matching logic; network mocked)."""

from unittest.mock import patch

from data.fetchers import kuvera
from data.fetchers.kuvera import _candidate_codes, normalize_tokens


def test_normalize_tokens_bridges_amfi_and_kuvera_conventions():
    amfi = "ICICI Prudential Technology Fund - Direct Plan -  Growth"  # double space, punctuation
    kuv = "ICICI Prudential Technology Growth Direct Plan"
    assert normalize_tokens(amfi) == normalize_tokens(kuv)


def test_normalize_tokens_distinguishes_plans_and_options():
    direct = normalize_tokens("X Fund - Direct Plan - Growth")
    regular = normalize_tokens("X Fund - Regular Plan - Growth")
    idcw = normalize_tokens("X Fund - Direct Plan - IDCW")
    assert direct != regular
    assert direct != idcw


def _fake_index():
    return [
        (normalize_tokens("Alpha Beta Growth Direct Plan"), "AB-GR"),
        (normalize_tokens("Alpha Beta IDCW Payout Direct Plan"), "AB-DP"),
        (normalize_tokens("Gamma Delta Growth Direct Plan"), "GD-GR"),
    ]


def test_candidate_codes_exact_match_first():
    with patch.object(kuvera, "_code_index", _fake_index):
        codes = _candidate_codes("Alpha Beta Fund - Direct Plan - Growth")
    assert codes[0] == "AB-GR"


def test_candidate_codes_no_weak_matches():
    with patch.object(kuvera, "_code_index", _fake_index):
        assert _candidate_codes("Totally Unrelated Fund - Direct - Growth") == []


def test_fetch_metadata_rejects_isin_mismatch():
    detail = [{"ISIN": "INF_WRONG", "aum": 1000.0, "expense_ratio": 0.5, "slug": "x"}]

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return detail

    with (
        patch.object(kuvera, "_code_index", _fake_index),
        patch.object(kuvera.httpx, "get", return_value=_Resp()),
    ):
        out = kuvera.fetch_fund_metadata_kuvera("Alpha Beta Fund - Direct Plan - Growth", ("INF_RIGHT",))
    assert out is None  # name matched, ISIN did not → refused


def test_fetch_metadata_requires_known_isin():
    assert kuvera.fetch_fund_metadata_kuvera("Anything", (None,)) is None
