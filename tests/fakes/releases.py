"""Fake of the ``ReleaseSource`` port (ADR 0029, 0067): a listing the test sets up."""

from collections.abc import Sequence

from app.domain.ports import IntegrationError
from app.domain.updates import ListedRelease, Release


class FakeReleases:
    """``ReleaseSource`` fake: lists ``release`` and ``more``, counts the calls, fails on demand.

    Each listed release carries the notes ``notes of <version>``; ``support_json`` is the
    ``support.json`` it serves (ADR 0116). While ``error`` is set, every
    listing raises it, as a refusing or unreachable GitHub would.
    """

    def __init__(self, release: Release | None, *, more: Sequence[Release] = ()) -> None:
        self.release = release
        self.more = list(more)
        self.error: IntegrationError | None = None
        self.calls = 0
        self.support_json: str | None = None

    async def listing(self) -> list[ListedRelease]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        found = [r for r in (self.release, *self.more) if r is not None]
        return [ListedRelease(r.version, r.url, None, f"notes of {r.version}") for r in found]

    async def support(self) -> str | None:
        if self.error is not None:
            raise self.error
        return self.support_json
