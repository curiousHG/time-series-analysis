"""Exceptions for the data layer."""

from __future__ import annotations


class UpstreamFormatError(RuntimeError):
    """An upstream source returned a payload whose shape no longer matches what we parse.

    Raised instead of returning empty or partially-filled data. AMFI's 2025 move of the
    plan/option suffix out of the scheme name into new `Plan`/`Option` columns shifted NAV and
    date one place right; the parser kept "succeeding" while silently nulling every NAV and
    flattening 14K scheme names onto 3.7K duplicates, and the screener quietly lost 98% of its
    rows. A format change has to stop a sync, not degrade one.

    Checks belong on the *parsed output* as well as the input: that failure produced a
    structurally valid, correctly-sized, almost entirely empty result.
    """

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(f"{source} response format changed — {detail}")
        self.source = source
        self.detail = detail


class SourceNotFound(LookupError):
    """The upstream has no record for the requested key.

    Distinct from `UpstreamFormatError`: the source answered normally, it just doesn't know
    this fund. AdvisorKhoj in particular answers an unknown scheme with HTTP 200 and its
    homepage, which the scraper used to parse into a row of Nones and mark "available".
    """

    def __init__(self, source: str, key: str) -> None:
        super().__init__(f"{source} has no record for {key!r}")
        self.source = source
        self.key = key


class SourceThrottled(RuntimeError):
    """The upstream kept rate-limiting (HTTP 429) after every backoff.

    Transient, unlike `SourceNotFound`: the fund exists, the source just refused to serve it
    right now. MFAPI throttles a bulk NAV refresh; falling back to AdvisorKhoj on a 429 turned
    the throttle into a misleading "no record" miss and could mark a new fund unavailable.
    """

    def __init__(self, source: str, attempts: int) -> None:
        super().__init__(f"{source} rate-limited the request (HTTP 429) {attempts} times in a row")
        self.source = source
        self.attempts = attempts
