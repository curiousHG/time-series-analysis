"""Exceptions for the data layer."""

from __future__ import annotations


class UpstreamFormatError(RuntimeError):
    """An upstream payload's shape no longer matches what we parse; raised instead of returning partial data."""

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(f"{source} response format changed — {detail}")
        self.source = source
        self.detail = detail


class SourceNotFound(LookupError):
    """The upstream answered normally but has no record for the requested key."""

    def __init__(self, source: str, key: str) -> None:
        super().__init__(f"{source} has no record for {key!r}")
        self.source = source
        self.key = key


class SourceThrottled(RuntimeError):
    """The upstream kept rate-limiting (HTTP 429) after every backoff; transient, unlike `SourceNotFound`."""

    def __init__(self, source: str, attempts: int) -> None:
        super().__init__(f"{source} rate-limited the request (HTTP 429) {attempts} times in a row")
        self.source = source
        self.attempts = attempts
