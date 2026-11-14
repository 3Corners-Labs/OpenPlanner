"""Variants of the admin site: plain export, a fake Webex, and the privacy sweep's marker bookings.

The site itself (``site``, on the database ``admin_db``) is in ``tests/conftest.py``, because
the web tests use it too; the harness, its admins and helpers are in
``tests/support/admin_site.py``. ``marked`` is the site with the marker bookings of the privacy
sweeps (ADR 0063).
"""

from collections.abc import AsyncIterator

import pytest

from app.adapters.integrations import Integrations
from app.config import Settings
from tests.fakes.integrations import FakeVideo
from tests.support.admin_site import Site, running_site
from tests.support.privacy_sweep import RELEASED, Fixture, mark


@pytest.fixture
async def plain_site(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    """The demo site on a host that allows the export without encryption (ADR 0099 point 8a)."""
    allowed = settings.model_copy(update={"configuration_plaintext_export_allowed": True})
    async with running_site(allowed, admin_db) as running:
        yield running


@pytest.fixture
def video() -> FakeVideo:
    """The fake Webex that ``video_site`` talks to."""
    return FakeVideo()


@pytest.fixture
async def video_site(settings: Settings, admin_db: str, video: FakeVideo) -> AsyncIterator[Site]:
    """The app wired to ``video``, as when a Webex Service App is configured."""
    async with running_site(settings, admin_db, Integrations(video=video)) as running:
        yield running


@pytest.fixture
async def marked(
    settings: Settings, admin_db: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Site, Fixture]]:
    """The demo site with the marker bookings of ``tests.support.privacy_sweep``.

    It runs release ``RELEASED`` while a newer one is known, so every page carries the update
    banner (ADR 0029). It starts its own site on ``admin_db``, so a test asks for ``marked`` or
    for ``site``, not both.
    """
    released = settings.model_copy(update={"app_version": RELEASED})
    async with running_site(released, admin_db) as running:
        yield running, await mark(running, monkeypatch)
