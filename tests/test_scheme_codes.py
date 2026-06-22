"""Unit tests for data.repositories.scheme_codes (DB mocked)."""


class _Result:
    def __init__(self, value):
        self._value = value

    def one(self):
        return self._value


class FakeSession:
    """Stand-in for a SQLModel session covering the mint path.

    `exec` returns the configured min(scheme_code) for the lookup query and records
    every pg_insert it sees. Doubles as a context manager so it also works under
    `with get_session() as session:`.
    """

    def __init__(self, min_code: int = 0):
        self.min_code = min_code
        self.inserts: list[dict] = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def exec(self, stmt):
        params = stmt.compile().params
        if "scheme_name" in params:  # pg_insert into amfi_schemes
            self.inserts.append(params)
            return _Result(None)
        return _Result(self.min_code)  # select(func.min(scheme_code))

    def commit(self):
        self.committed = True


def test_resolve_codes_empty_short_circuits():
    from data.repositories import scheme_codes

    assert scheme_codes.resolve_codes([]) == {}


def test_resolve_codes_maps_names_to_codes(monkeypatch):
    from data.repositories import scheme_codes

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def exec(self, stmt):
            return _Rows([("Alpha Fund", 1), ("Beta Fund", 2)])

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    monkeypatch.setattr(scheme_codes, "get_session", lambda: Session())
    assert scheme_codes.resolve_codes(["Alpha Fund", "Beta Fund"]) == {"Alpha Fund": 1, "Beta Fund": 2}


def test_mint_synthetic_codes_allocates_descending_negatives():
    from data.repositories import scheme_codes

    session = FakeSession(min_code=0)
    out = scheme_codes.mint_synthetic_codes(session, ["A Fund", "B Fund"])

    assert out == {"A Fund": -1, "B Fund": -2}
    assert [i["scheme_code"] for i in session.inserts] == [-1, -2]
    assert [i["scheme_name"] for i in session.inserts] == ["A Fund", "B Fund"]


def test_mint_synthetic_codes_continues_below_existing_min():
    from data.repositories import scheme_codes

    session = FakeSession(min_code=-5)
    assert scheme_codes.mint_synthetic_codes(session, ["X"]) == {"X": -6}


def test_mint_synthetic_codes_empty_is_noop():
    from data.repositories import scheme_codes

    session = FakeSession(min_code=0)
    assert scheme_codes.mint_synthetic_codes(session, []) == {}
    assert session.inserts == []


def test_resolve_or_mint_code_returns_existing(monkeypatch):
    from data.repositories import scheme_codes

    monkeypatch.setattr(scheme_codes, "resolve_codes", lambda names: {names[0]: 42})
    assert scheme_codes.resolve_or_mint_code("Known Fund") == 42


def test_resolve_or_mint_code_mints_when_absent(monkeypatch):
    from data.repositories import scheme_codes

    session = FakeSession(min_code=0)
    monkeypatch.setattr(scheme_codes, "resolve_codes", lambda names: {})
    monkeypatch.setattr(scheme_codes, "clear_slug_cache", lambda: None)
    monkeypatch.setattr(scheme_codes, "get_session", lambda: session)

    assert scheme_codes.resolve_or_mint_code("New Fund") == -1
    assert session.committed is True


def test_resolve_codes_with_synthetic_mints_only_missing(monkeypatch):
    from data.repositories import scheme_codes

    monkeypatch.setattr(scheme_codes, "resolve_codes", lambda names: {"Known": 7})
    monkeypatch.setattr(scheme_codes, "clear_slug_cache", lambda: None)
    monkeypatch.setattr(scheme_codes, "get_session", lambda: FakeSession(min_code=0))

    out = scheme_codes.resolve_codes_with_synthetic(["Known", "Missing"])
    assert out == {"Known": 7, "Missing": -1}
