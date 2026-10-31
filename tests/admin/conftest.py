"""An app with the demo data, a fake identity provider and helpers to log in as any role."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.authorization import Principal, Role
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.fakes.dns import FakeTxtResolver
from tests.fakes.identity import FakeIdentityProvider
from tests.fakes.integrations import FakeVideo
from tests.support.database import migrated_database
from tests.support.domain import NOW

SUPER = Principal("sub-super", "Sanne Super", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = Principal(
    "sub-org", "Olaf Org", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset()
)
GROUP_ADMIN = Principal(
    "sub-group", "Gina Group", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"})
)
OUTSIDER = Principal(
    "sub-outsider", "Otto Other", frozenset({Role.ORG_ADMIN}), frozenset({"andere"}), frozenset()
)
AUDITOR = Principal(
    "sub-auditor", "Anouk Audit", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset()
)
AGENT = Principal(
    "sub-agent", "Mo Medewerker", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"})
)
NO_ROLE = Principal("sub-none", "Nina None", frozenset(), frozenset({"demo"}), frozenset({"demo"}))

SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


class Site:
    """The app, its fake identity provider, clock and DNS resolver (ADR 0073)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        idp: FakeIdentityProvider,
        clock: FixedClock,
        dns: FakeTxtResolver | None = None,
    ):
        self.client = client
        self.idp = idp
        self.clock = clock
        self.dns = dns or FakeTxtResolver()

    async def login_as(self, principal: Principal) -> httpx.Response:
        self.idp.next_principal = principal
        start = await self.client.get("/admin/login")
        assert start.status_code == 303
        return await self.client.get("/admin/callback", params={"code": "c", "state": "state-1"})


@pytest.fixture
def admin_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "admin")


@asynccontextmanager
async def _running_site(
    settings: Settings, admin_db: str, integrations: Integrations | None = None
) -> AsyncIterator[Site]:
    """Seed the demo data and run the app, with explicit integration adapters if given."""
    engine = create_engine(admin_db)
    await seed_demo(engine)
    await engine.dispose()
    idp, clock, dns = FakeIdentityProvider(), FixedClock(NOW), FakeTxtResolver()
    app = create_app(
        settings.model_copy(update={"database_url": admin_db}),
        clock,
        idp,
        integrations=integrations,
        mail_domain_resolver=dns,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield Site(client, idp, clock, dns)


@pytest.fixture
async def site(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    async with _running_site(settings, admin_db) as running:
        yield running


@pytest.fixture
def video() -> FakeVideo:
    """The fake Webex that ``video_site`` talks to."""
    return FakeVideo()


@pytest.fixture
async def video_site(settings: Settings, admin_db: str, video: FakeVideo) -> AsyncIterator[Site]:
    """The app wired to ``video``, as when a Webex Service App is configured."""
    async with _running_site(settings, admin_db, Integrations(video=video)) as running:
        yield running
