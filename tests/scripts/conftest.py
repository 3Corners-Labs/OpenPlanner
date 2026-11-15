"""Fixtures shared by the tests of the scripts."""

import pytest

from scripts import release_plan


@pytest.fixture(autouse=True)
def published_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat every release tag as published, unless a test says otherwise (no `gh`, no network)."""
    monkeypatch.setattr(release_plan, "release_published", lambda _tag: True)
