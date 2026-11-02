"""Two organisations that differ in everything, served at the same time (ASVS V8, TEN-03).

Organisation A (``demo``) and organisation B (``andere``) differ in name, colour, logo, font,
text overrides, agent term, group time zone, clock style, sender (own SMTP against the
environment fallback), custom form field and contact options. Hundreds of requests for both run
through ``asyncio.gather``: the participant pages, the widget API, the admin portal (one admin
per organisation) and a request that starts a background task. The worker renders the booking
mails of both at once with the real ``JobRunner``, a fake ``Clock`` and a recording SMTP sender.

Every response and every message may hold markers of its own organisation only. The clock style
is a ``ContextVar`` holding a mutable list (``app.i18n.dates``); a task started inside a request
inherits that list, so the background request proves it stays inside its own request.
"""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import senders as senders_module
from app.adapters.db.jobs import SqlJobQueue
from app.adapters.db.models import GroupRow, OrganisationOriginRow, OrganisationRow
from app.adapters.db.text_overrides import CachedTextOverrides
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import secret_box
from app.adapters.senders import ConfiguredSenders
from app.adapters.smtp import SmtpConfig
from app.domain.form_fields import FieldDefinition, FieldType, FormDefinition
from app.domain.services import ContactInput
from app.i18n import Translator, load_catalogues
from app.i18n.dates import ClockStyle, clock_scope, format_clock, use_clock
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from app.keys import management_token_key
from app.web.organisation_settings import follow_group_from_path
from tests.admin.conftest import ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.fakes.messaging import SmtpRecorder
from tests.fakes.woff2 import make_woff2

MIN_REQUESTS = 200
BACKGROUND_PATH = "/__probe/{slug}/background"
MOMENT = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
TWELVE_HOUR = re.compile(r"\b\d{1,2}:\d{2}\s?[ap]\.?m\.?", re.IGNORECASE)
"""A 12-hour clock time: ``9:00 a.m.`` in Dutch, ``9:00 AM`` in English (Babel)."""
PNG = b"\x89PNG\r\n\x1a\n"
FALLBACK = SmtpConfig(
    host="smtp.platform-fallback.example", port=25, sender="no-reply@platform.example"
)
"""The environment's SMTP server, used by an organisation without its own."""
Check = Callable[[httpx.Response], None]


@dataclass(frozen=True)
class Org:
    """One organisation of the proof, with everything that sets it apart from the other."""

    slug: str
    group: str
    word: str  # the marker prefix; unique per organisation
    colour: str
    clock: ClockStyle
    zone: str
    agent: str
    agents: str
    smtp_host: str | None
    admin: Any  # the org admin who works in the portal during the proof
    origin: str
    phone: str
    first_slot_utc: str
    logo: bytes = field(repr=False, default=b"")
    font: bytes = field(repr=False, default=b"")

    @property
    def name(self) -> str:
        return f"{self.word} Gemeente"

    @property
    def group_name(self) -> str:
        return f"{self.word} loket"

    @property
    def intro(self) -> str:
        return f"{self.word} intro: kies een moment van {{minutes}} minuten."

    @property
    def field_label(self) -> str:
        return f"{self.word} kenmerk"

    @property
    def contact_site(self) -> str:
        return f"https://{self.word.lower()}.example/contact"

    @property
    def markers(self) -> tuple[str, ...]:
        """Strings that appear for this organisation only."""
        found = [
            self.word,
            self.colour,
            self.agents,
            self.phone,
            self.origin,
            self.contact_site,
        ]
        if self.smtp_host:
            found.append(self.smtp_host)
        return tuple(found)


A = Org(
    slug="demo",
    group="demo",
    word="Zqalfa",
    colour="#154273",
    clock=ClockStyle.H24,
    zone="Europe/Amsterdam",
    agent="behandelaar",
    agents="behandelaars",
    smtp_host="smtp.zqalfa.example",
    admin=ORG_ADMIN,
    origin="https://www.zqalfa.example",
    phone="14 020",
    first_slot_utc="2026-01-02T08:00:00Z",
    logo=PNG + b"\x00" * 64,
    font=make_woff2(0),
)
B = Org(
    slug="andere",
    group="andere-team",
    word="Zqbravo",
    colour="#6a1b9a",
    clock=ClockStyle.H12,
    zone="America/New_York",
    agent="consulent",
    agents="consulenten",
    smtp_host=None,
    admin=OUTSIDER,
    origin="https://www.zqbravo.example",
    phone="14 030",
    first_slot_utc="2026-01-02T14:00:00Z",
    logo=PNG + b"\x01" * 64,
    font=make_woff2(1),
)
ORGS = (A, B)
SMTP = {
    "smtp_host": "smtp.zqalfa.example",
    "smtp_port": "587",
    "smtp_security": "starttls",
    "smtp_username": "afspraak",
    "smtp_credential": "Geheim-wachtwoord-123",  # gitleaks:allow (test value)
    "sender_address": "afspraak@zqalfa.example",
    "sender_name": "Zqalfa Gemeente",
}


def other(org: Org) -> Org:
    return B if org is A else A


@pytest.fixture
async def proof(site: Site) -> Site:
    """The app with both organisations configured, and the background probe route added."""
    app: FastAPI = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await _configure(site, app)

    async def background(request: Request, slug: str) -> dict[str, str]:
        async def later() -> str:
            await asyncio.sleep(0.005)  # other requests run meanwhile
            return format_clock(MOMENT, UTC, "nl")

        task = asyncio.create_task(later())  # inherits the request's clock scope
        await asyncio.sleep(0)
        return {"clock": await task}

    app.router.add_api_route(
        BACKGROUND_PATH, background, methods=["GET"], dependencies=[Depends(follow_group_from_path)]
    )
    return site


async def _configure(site: Site, app: FastAPI) -> None:
    """Give each organisation its own branding, texts, settings, sender, form and contacts."""
    await site.login_as(SUPER)
    post = site.client.post
    async with AsyncSession(app.state.engine) as session, session.begin():
        for org in ORGS:
            await session.execute(
                update(GroupRow)
                .where(GroupRow.slug == org.group)
                .values(name=org.group_name, timezone=org.zone, embedding_enabled=True)
            )
            organisation_id = await session.scalar(
                select(OrganisationRow.id).where(OrganisationRow.slug == org.slug)
            )
            session.add(
                OrganisationOriginRow(
                    organisation_id=organisation_id, origin=org.origin, position=0
                )
            )
    engine = app.state.engine
    for org in ORGS:
        base = f"/admin/organisations/{org.slug}"
        steps = (
            (f"{base}/branding", {"display_name": org.name, "accent_color": org.colour}),
            (
                f"{base}/texts/nl",
                {
                    "group.intro": org.intro,
                    "term.agent": org.agent,
                    "term.agents": org.agents,
                },
            ),
            (f"{base}/settings/time_format", {"value": org.clock.value}),
            (
                f"{base}/contacts",
                {
                    "heading_nl": f"{org.word} bereik ons",
                    "contact_1_kind": "phone",
                    "contact_1_value": org.phone,
                    "contact_2_kind": "website",
                    "contact_2_value": org.contact_site,
                },
            ),
        )
        for path, data in steps:
            assert (await post(path, data=data, headers=SAME_ORIGIN)).status_code == 303, path
        for path, name, content, kind in (
            (f"{base}/branding/logo", "logo", org.logo, "image/png"),
            (f"{base}/branding/font/heading", "font", org.font, "font/woff2"),
        ):
            sent = await post(
                path, files={name: (f"{name}.bin", content, kind)}, headers=SAME_ORIGIN
            )
            assert sent.status_code == 303, path
    smtp = await post(
        f"/admin/organisations/{A.slug}/channels/email", data=SMTP, headers=SAME_ORIGIN
    )
    assert smtp.status_code == 303
    await app.state.live_settings.refresh()  # other processes follow within RELOAD_SECONDS
    uow = SqlUnitOfWork(engine)
    for org in ORGS:
        group = await app.state.booking_service.get_group(org.group)
        async with uow.locked() as tx:
            await tx.save_form(
                group.id,
                FormDefinition(
                    2,
                    (
                        FieldDefinition.create(
                            key="kenmerk",
                            type=FieldType.SHORT_TEXT,
                            labels={"nl": org.field_label, "en": org.field_label},
                        ),
                    ),
                ),
            )


async def public_client(site: Site) -> httpx.AsyncClient:
    """Return a client without cookies, as one more visitor."""
    return httpx.AsyncClient(
        transport=site.client._transport,  # type: ignore[attr-defined]  # noqa: SLF001
        base_url="http://testserver",
    )


async def admin_clients(site: Site) -> dict[str, httpx.AsyncClient]:
    """Log in one org admin per organisation, each in a client of their own."""
    clients: dict[str, httpx.AsyncClient] = {}
    for org in ORGS:
        client = await public_client(site)
        await Site(client, site.idp, site.clock, site.dns).login_as(org.admin)
        clients[org.slug] = client
    return clients


def only_own(org: Org, response: httpx.Response, *, binary: bool = False) -> None:
    """Fail when the response holds a marker of the other organisation."""
    assert response.status_code == 200, (org.slug, response.request.url, response.status_code)
    if binary:
        return
    text = response.text.lower()
    for marker in other(org).markers:
        assert marker.lower() not in text, (org.slug, str(response.request.url), marker)


def has(org: Org, *needles: str) -> Check:
    def check(response: httpx.Response) -> None:
        only_own(org, response)
        for needle in needles:
            assert needle in response.text, (org.slug, str(response.request.url), needle)

    return check


def page_check(org: Org) -> Check:
    def check(response: httpx.Response) -> None:
        only_own(org, response)
        text = response.text
        assert org.name in text
        assert org.group_name in text
        assert f"/o/{org.slug}/theme.css" in text
        assert bool(TWELVE_HOUR.search(text)) is (org.clock is ClockStyle.H12)

    return check


def exact(org: Org, content: bytes) -> Check:
    def check(response: httpx.Response) -> None:
        only_own(org, response, binary=True)
        assert response.content == content

    return check


def widget_check(org: Org) -> Check:
    def check(response: httpx.Response) -> None:
        only_own(org, response)
        assert response.headers["access-control-allow-origin"] == org.origin
        assert org.group_name in response.text
        assert org.field_label in response.text

    return check


def times_check(org: Org) -> Check:
    def check(response: httpx.Response) -> None:
        only_own(org, response)
        first = response.json()["times"]["groups"][0]["times"][0]
        assert first["start"] == org.first_slot_utc

    return check


def foreign_origin_check(response: httpx.Response) -> None:
    assert "access-control-allow-origin" not in response.headers
    assert response.status_code in (400, 403, 404)


def background_check(org: Org) -> Check:
    def check(response: httpx.Response) -> None:
        assert response.status_code == 200, response.text
        with clock_scope():
            use_clock(org.clock)
            expected = format_clock(MOMENT, UTC, "nl")
        assert response.json() == {"clock": expected}

    return check


@dataclass(frozen=True)
class Case:
    """One request and what its response must show."""

    org: Org
    path: str
    client: httpx.AsyncClient
    check: Check
    headers: dict[str, str] = field(default_factory=dict)

    async def run(self) -> None:
        self.check(await self.client.get(self.path, headers=self.headers))


def cases(visitor: httpx.AsyncClient, admins: dict[str, httpx.AsyncClient]) -> list[Case]:
    """Return one round of requests per organisation: public, widget, admin and background."""
    found: list[Case] = []
    for org in ORGS:
        admin = admins[org.slug]
        origin = {"origin": org.origin}
        widget = f"/api/v1/widget/{org.slug}/{org.group}"
        base = f"/admin/organisations/{org.slug}"
        found += [
            Case(org, f"/g/{org.group}", visitor, page_check(org)),
            Case(org, f"/g/{org.group}?lang=en", visitor, page_check(org)),
            Case(org, f"/o/{org.slug}/theme.css", visitor, has(org, org.colour.upper())),
            Case(org, f"/o/{org.slug}/logo", visitor, exact(org, org.logo)),
            Case(org, f"/o/{org.slug}/font/heading", visitor, exact(org, org.font)),
            Case(org, f"{widget}/", visitor, widget_check(org), origin),
            Case(
                org,
                f"{widget}/availability?day=2026-01-02",
                visitor,
                times_check(org),
                origin,
            ),
            Case(
                org,
                f"{widget}/",
                visitor,
                foreign_origin_check,
                {"origin": other(org).origin},
            ),
            Case(org, f"{base}/texts/nl", admin, has(org, org.intro, org.agents)),
            Case(org, f"/admin/groups/{org.group}/staffing", admin, has(org, org.agents)),
            Case(org, f"{base}/contacts", admin, has(org, org.phone, org.contact_site)),
            Case(
                org,
                f"{base}/settings",
                admin,
                has(org, "24" if org.clock is ClockStyle.H24 else "12"),
            ),
            Case(
                org,
                BACKGROUND_PATH.format(slug=org.group),
                visitor,
                background_check(org),
            ),
        ]
    return found


async def test_both_organisations_stay_apart_under_concurrent_requests(proof: Site) -> None:
    visitor = await public_client(proof)
    admins = await admin_clients(proof)
    rounds = -(-MIN_REQUESTS // len(cases(visitor, admins)))
    batch = [case for _ in range(rounds) for case in cases(visitor, admins)]
    assert len(batch) >= MIN_REQUESTS

    await asyncio.gather(*(case.run() for case in batch))

    await asyncio.gather(visitor.aclose(), *(c.aclose() for c in admins.values()))


# --- the worker --------------------------------------------------------------------------


BOOKINGS_PER_ORG = 30
SLOT_TIMES = ((9, 0), (9, 30), (10, 0), (10, 30), (11, 0), (11, 30))


def slot_starts(org: Org) -> list[datetime]:
    """Return 30 slot starts (UTC): six per weekday, 5-9 January 2026, in the group's zone."""
    zone = ZoneInfo(org.zone)
    days = [date(2026, 1, 5) + timedelta(days=n) for n in range(5)]
    return [
        datetime(d.year, d.month, d.day, h, m, tzinfo=zone).astimezone(UTC)
        for d in days
        for h, m in SLOT_TIMES
    ]


async def test_the_worker_renders_both_organisations_mails_at_once(
    proof: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    app: FastAPI = proof.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    recorder = SmtpRecorder()
    monkeypatch.setattr(senders_module, "SmtpMailSender", recorder)
    service = app.state.booking_service
    for org in ORGS:
        for number, start in enumerate(slot_starts(org)[:BOOKINGS_PER_ORG]):
            contact = ContactInput(
                first_name=f"{org.word}ada{number}",
                last_name="Lovelace",
                email=f"{org.word.lower()}{number}@{org.word.lower()}.example",
                phone=None,
            )
            receipt = await service.book(
                group_slug=org.group, start=start, contact=contact, locale="nl"
            )
            await service.confirm(receipt.management_token)

    settings = app.state.settings
    uow = SqlUnitOfWork(app.state.engine, secret_box(settings))
    live = app.state.live_settings
    async with httpx.AsyncClient() as http:
        runner = JobRunner(
            SqlJobQueue(app.state.engine),
            uow,
            proof.clock,
            ConfiguredSenders(uow, http, lambda: FALLBACK),
            MessageComposer(
                Translator(load_catalogues(), CachedTextOverrides(uow)),
                management_token_key(settings),
                settings.public_base_url,
            ),
            clock_style=lambda organisation: live.for_organisation(organisation).time_format,
        )
        claimed = await asyncio.gather(*(runner.run_due() for _ in range(8)))

    assert sum(claimed) >= 2 * BOOKINGS_PER_ORG
    assert len(recorder.deliveries) == sum(claimed)
    for delivery in recorder.deliveries:
        org = A if A.word.lower() in delivery.message.recipient else B
        text = f"{delivery.message.subject}\n{delivery.message.text}\n{delivery.message.html}"
        assert org.group_name in text
        assert other(org).group_name not in text
        assert other(org).word not in text
        assert bool(TWELVE_HOUR.search(delivery.message.text)) is (org.clock is ClockStyle.H12)
        if org.smtp_host:
            assert delivery.config.host == org.smtp_host
            assert delivery.config.sender.endswith("<afspraak@zqalfa.example>")
        else:
            assert delivery.config == FALLBACK
    served = {A.word.lower(): 0, B.word.lower(): 0}
    for delivery in recorder.deliveries:
        served[delivery.message.recipient.split("@")[1].split(".")[0]] += 1
    assert min(served.values()) >= BOOKINGS_PER_ORG


# --- the clock style across tasks ---------------------------------------------------------


async def test_clock_scopes_of_concurrent_tasks_never_share_their_style() -> None:
    async def one(style: ClockStyle) -> str:
        with clock_scope():
            use_clock(style)
            task = asyncio.create_task(_later())  # inherits this scope's list
            await asyncio.sleep(0)
            return await task

    async def _later() -> str:
        await asyncio.sleep(0.001)
        return format_clock(MOMENT, UTC, "nl")

    styles = [ClockStyle.H24, ClockStyle.H12] * 100
    results = await asyncio.gather(*(one(style) for style in styles))

    assert results == ["14:30", "2:30 p.m."] * 100


async def test_a_task_shares_the_style_of_its_own_scope_only() -> None:
    with clock_scope():
        use_clock(ClockStyle.H24)
        child = asyncio.create_task(_style_after_change())
        await asyncio.sleep(0)
        with clock_scope():  # another request's scope, started in between
            use_clock(ClockStyle.H12)
            assert format_clock(MOMENT, UTC, "nl") == "2:30 p.m."
        assert await child == "14:30"


async def _style_after_change() -> str:
    await asyncio.sleep(0.001)
    return format_clock(MOMENT, UTC, "nl")
