"""The admin site of the HTTP tests: the app in-process with the demo data, and its admins.

``running_site`` seeds the demo data and runs the app with a fake identity provider, clock and
DNS resolver; ``Site`` is what it yields, and ``Site.browser`` is the same site in another
browser. The fixtures that start one are ``site`` and ``admin_db`` in ``tests/conftest.py`` (the
admin and the web tests share them) and the variants in ``tests/admin/conftest.py``. The admins
below log in through ``Site.login_as``; a form post, the site's database and its audit log come
with them.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Principal, Role, Scope
from app.domain.entitlements import LoginClaims
from app.main import create_app
from tests.fakes.clock import FixedClock
from tests.fakes.dns import FakeTxtResolver
from tests.fakes.identity import FakeIdentityProvider
from tests.fakes.identity_providers import (
    FakeDiscoveryFetcher,
    FakeOrganisationProviders,
    FakeSigningKeys,
)
from tests.fakes.log_destinations import FakeFactory
from tests.fakes.staff_directory import FakeStaffDirectory
from tests.support.app_client import in_process, served
from tests.support.domain import NOW
from tests.support.principals import legacy_claims, principal

SUPER = principal("sub-super", "Sanne Super", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = principal(
    "sub-org", "Olaf Org", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset()
)
GROUP_ADMIN = principal(
    "sub-group", "Gina Group", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"})
)
OUTSIDER = principal(
    "sub-outsider", "Otto Other", frozenset({Role.ORG_ADMIN}), frozenset({"andere"}), frozenset()
)
AUDITOR = principal(
    "sub-auditor", "Anouk Audit", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset()
)
AGENT = principal(
    "sub-agent", "Mo Medewerker", frozenset({Role.AGENT}), frozenset(), frozenset({"demo"})
)
NO_ROLE = principal("sub-none", "Nina None", frozenset(), frozenset({"demo"}), frozenset({"demo"}))

SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
"""The header of a form the portal posts from its own pages."""


class Site:
    """The app, its client, fake identity provider, clock and DNS resolver (ADR 0073)."""

    def __init__(
        self,
        app: FastAPI,
        client: httpx.AsyncClient,
        idp: FakeIdentityProvider,
        clock: FixedClock,
        dns: FakeTxtResolver | None = None,
        discovery: FakeDiscoveryFetcher | None = None,
        providers: FakeOrganisationProviders | None = None,
        directory: FakeStaffDirectory | None = None,
        log_senders: FakeFactory | None = None,
    ):
        self.app = app
        """The application the client talks to; its ``state`` holds the engine and services."""
        self.client = client
        self.idp = idp
        self.clock = clock
        self.dns = dns or FakeTxtResolver()
        self.discovery = discovery or FakeDiscoveryFetcher()
        """The discovery documents the registration check sees (ADR 0083 step 2)."""
        self.providers = providers or FakeOrganisationProviders()
        """The adapters of the registered organisation providers, for the test login."""
        self.directory = directory
        """The staff directory of the bundled provider, when the test has one (ADR 0095)."""
        self.log_senders = log_senders or FakeFactory()
        """The senders the log destinations deliver through (ADR 0085): fakes, no network."""

    def browser(self, peer: str | None = None) -> "Site":
        """Return this site in another browser: no session, its own cookie jar.

        Its requests come from the address ``peer`` if one is given; the app, identity provider,
        clock and the other fakes are this site's own.
        """
        client = httpx.AsyncClient(
            transport=in_process(self.app, peer), base_url=self.client.base_url
        )
        return Site(
            self.app,
            client,
            self.idp,
            self.clock,
            self.dns,
            self.discovery,
            self.providers,
            self.directory,
            self.log_senders,
        )

    async def login_as(self, principal: Principal) -> httpx.Response:
        """Log in as ``principal`` through the legacy claims that resolve to it."""
        return await self.login_with(legacy_claims(principal))

    async def login_with(self, claims: LoginClaims) -> httpx.Response:
        """Log in with exactly ``claims`` (entitlement values, ambiguous claims; ADR 0049)."""
        self.idp.next_claims = claims
        # ``platform``: straight to the platform provider, also when domains route (ADR 0083).
        start = await self.client.get("/admin/login", params={"platform": "1"})
        assert start.status_code == 303
        return await self.client.get("/admin/callback", params={"code": "c", "state": "state-1"})


@asynccontextmanager
async def running_site(
    settings: Settings,
    admin_db: str,
    integrations: Integrations | None = None,
    directory: FakeStaffDirectory | None = None,
) -> AsyncIterator[Site]:
    """Seed the demo data and run the app, with explicit integration adapters if given."""
    engine = create_engine(admin_db)
    await seed_demo(engine)
    await engine.dispose()
    idp, clock, dns = FakeIdentityProvider(), FixedClock(NOW), FakeTxtResolver()
    discovery, providers = FakeDiscoveryFetcher(), FakeOrganisationProviders()
    log_senders = FakeFactory()
    app = create_app(
        settings.model_copy(update={"database_url": admin_db}),
        clock,
        idp,
        integrations=integrations,
        mail_domain_resolver=dns,
        discovery_fetcher=discovery,
        signing_keys=FakeSigningKeys(),
        organisation_providers=providers,  # type: ignore[arg-type]
        staff_directory=directory,
        log_sender_factory=log_senders,
    )
    async with served(app) as client:
        yield Site(app, client, idp, clock, dns, discovery, providers, directory, log_senders)


def site_uow(site: Site) -> SqlUnitOfWork:
    """Return a unit of work on the site's database."""
    return SqlUnitOfWork(site.app.state.engine)


async def post_form(site: Site, path: str, data: dict[str, str] | None = None) -> httpx.Response:
    """Post ``data`` (nothing if left out) to ``path`` as a form of the portal itself."""
    return await site.client.post(path, data=data or {}, headers=SAME_ORIGIN)


async def audit_log(site: Site, limit: int = 50) -> list[AuditEvent]:
    """Return the newest ``limit`` audit events of the site, oldest first."""
    async with site_uow(site).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=limit)
    return [e.event for e in reversed(events)]


AuditRow = tuple[str, str, Outcome, str]
"""An audit event as the tests compare it: actor, action, outcome and reason."""


async def audit_events(site: Site) -> list[AuditRow]:
    """Return actor, action, outcome and reason of the newest 50 audit events, oldest first."""
    return [(e.actor, e.action, e.outcome, e.reason) for e in await audit_log(site)]
