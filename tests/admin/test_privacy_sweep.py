"""Personal data never appears on overview, list, export or alert pages (ADR 0063 §2, §3).

Bookings whose personal-data fields hold unique marker values are stored in the demo group,
and every admin GET route is fetched as the superuser, who sees everything. A marker in a
response is a leak, unless ADR 0063 §4 allows it there: the booking detail page may show all
of it, the queue item the first name and the join link, and the group's own agenda, queue
and planner pages the first name only (``FIRST_NAME_PAGES``).

The templates are discovered from the file system: every admin template must have been
rendered during the sweep, so a template added later is covered without editing this test.
A route with path parameters needs a concrete path in ``PATHS``; a new one fails the sweep
until it is added. The whole file runs in the fast lane (no ``matrix`` marker).
"""

import re
from collections.abc import MutableMapping
from dataclasses import fields, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from jinja2 import Environment, Template

from app.adapters.db.uow import SqlUnitOfWork
from app.admin.overview_charts import CHART_SERIES, KIND_OF_VIEW
from app.admin.routes import PUBLIC_ADMIN_PATHS
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.audit import AuditFilter
from app.domain.authorization import MATRIX, Role
from app.domain.booking import ChangeReason, MeetingRef, TrialCall
from app.domain.form_fields import FieldDefinition, FieldType, FormDefinition
from app.domain.planning import PlanningService
from app.domain.privacy import ALIASES, PII_FIELDS
from app.domain.scheduling import DispatchKind, ScheduleOverride, TimeWindow
from app.domain.services import BookingService, ContactInput
from app.domain.staff import StaffFilter
from app.domain.updates import Release, ReleaseCheck, Version
from app.web.widgets import OVERVIEW_PATH, WIDGETS
from tests.admin.conftest import GROUP_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.domain import NOW, TEST_TOKEN_KEY, book_confirmed
from tests.support.routes import all_routes

ADMIN_TEMPLATES = Path("app/admin/templates")
TEMPLATE_NAMES = frozenset(
    str(p.relative_to(ADMIN_TEMPLATES)) for p in ADMIN_TEMPLATES.rglob("*.html")
)
"""Every admin template, found on disk; each must be rendered by the sweep."""

# The demo group is open on weekdays; the clock starts on Thursday 1 January 2026.
TODAY_0900 = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)  # Friday 09:00 Amsterdam: the queue
NEXT_WEEK_0900 = datetime(2026, 1, 9, 8, 0, tzinfo=UTC)  # the planner's pages
NEXT_WEEK_0930 = datetime(2026, 1, 9, 8, 30, tzinfo=UTC)  # awaiting a new time
CLOSED_DAY_0900 = datetime(2026, 1, 16, 8, 0, tzinfo=UTC)  # on hold by a closure

# Unique values that no template text, label or fixture contains by accident.
MARKERS = {
    "first_name": "Zqfirst7x",
    "last_name": "Zqlast7x",
    "email": "zqmail7x",  # one address per booking (ADR 0059 limit): zqmail7x-<n>@example.org
    "phone": "624681357",  # 06 2468 1357, stored as +31624681357
    "form_answers": "Zqanswer7x",
    "guest_join_url": "https://video.example/zqjoin7x",
    "host_join_url": "https://video.example/zqjoin7x",
    "trial_join_url": "https://video.example/zqtrial7x",
}
"""Marker per personal-data field (one join link: the adapter stores it under both)."""


def citizen(number: int) -> ContactInput:
    """The marker citizen; a different address per booking, each holding the marker."""
    return ContactInput(
        first_name=MARKERS["first_name"],
        last_name=MARKERS["last_name"],
        email=f"{MARKERS['email']}-{number}@example.org",
        phone="06 2468 1357",
        answers={"toelichting": MARKERS["form_answers"]},
    )


ROLE = {
    "template": Role.GROUP_ADMIN.value,
    "name_nl": "Teamleider",
    "name_en": "Team lead",
    "claim_value": "openplanner-custom",
    **{f"perm.{p}": "on" for p in MATRIX[Role.GROUP_ADMIN]},
}
SCENARIO = {
    "scenario_name": "Storing",
    "notice_nl": "Door een storing zijn wij gesloten.",
    "notice_en": "We are closed because of an outage.",
}
CLIENT = {
    "name": "WxCC AI Agent",
    "organisation": "demo",
    "lifetime_days": "90",
    "scope.availability:read": "on",
    "group.demo": "on",
}
CLOSURE = {
    "starts_at": "2026-01-16",
    "starts_at_time": "00:00",
    "ends_at": "2026-01-17",
    "ends_at_time": "00:00",
    "notice_nl": "Door een storing zijn wij gesloten.",
    "notice_en": "We are closed because of an outage.",
}

RELEASE = Release(Version(1, 4, 0), "https://github.com/acme/openplanner/releases/tag/v1.4.0")
"""A newer release than ``settings.app_version`` below, so every page carries the banner."""

DETAIL = "/admin/groups/demo/bookings/{id}"
QUEUE_ITEM = "/admin/groups/demo/queue/{queued}"
FIRST_NAME_PAGES = frozenset(
    {
        "/admin/groups/demo/agenda",
        "/admin/groups/demo/agenda/week",
        "/admin/groups/demo/agenda/waiting",
        "/admin/groups/demo/agenda/slot-rebook?start=2026-01-09T08:00:00Z",
        "/admin/groups/demo/agenda/bookings/{id}/move",
        "/admin/groups/demo/agenda/bookings/{id}/cancel",
        "/admin/groups/demo/agenda/bookings/{id}/rebook",
        "/admin/groups/demo/queue",
    }
)
"""The group's own booking pages for ``bookings.view`` and ``queue.work``: the first name
only, the same minimum that goes to the contact centre (ADR 0043, 0044; ADR 0063 §4)."""

PATHS: dict[str, str] = {
    "/admin/groups/{slug}": "/admin/groups/demo",
    "/admin/groups/{slug}/settings": "/admin/groups/demo/settings",
    "/admin/groups/{slug}/settings/form": "/admin/groups/demo/settings/form",
    "/admin/groups/{slug}/settings/form/{key}": "/admin/groups/demo/settings/form/onderwerp",
    "/admin/groups/{slug}/staffing": "/admin/groups/demo/staffing",
    "/admin/groups/{slug}/staffing/{rule_id}": "/admin/groups/demo/staffing/{rule}",
    "/admin/groups/{slug}/staffing/{rule_id}/split": "/admin/groups/demo/staffing/{rule}/split",
    "/admin/groups/{slug}/hours": "/admin/groups/demo/hours",
    "/admin/groups/{slug}/hours/{rule_id}": "/admin/groups/demo/hours/{rule}",
    "/admin/groups/{slug}/exceptions": "/admin/groups/demo/exceptions",
    "/admin/groups/{slug}/reminders": "/admin/groups/demo/reminders",
    "/admin/groups/{slug}/messages": "/admin/groups/demo/messages",
    "/admin/groups/{slug}/messages/{text_locale}": "/admin/groups/demo/messages/nl",
    "/admin/groups/{slug}/holidays": "/admin/groups/demo/holidays",
    "/admin/groups/{slug}/agenda": "/admin/groups/demo/agenda",
    "/admin/groups/{slug}/agenda/week": "/admin/groups/demo/agenda/week",
    "/admin/groups/{slug}/agenda/waiting": "/admin/groups/demo/agenda/waiting",
    "/admin/groups/{slug}/agenda/slot-rebook": (
        "/admin/groups/demo/agenda/slot-rebook?start=2026-01-09T08:00:00Z"
    ),
    "/admin/groups/{slug}/agenda/bookings/{booking_id}/move": (
        "/admin/groups/demo/agenda/bookings/{id}/move"
    ),
    "/admin/groups/{slug}/agenda/bookings/{booking_id}/cancel": (
        "/admin/groups/demo/agenda/bookings/{id}/cancel"
    ),
    "/admin/groups/{slug}/agenda/bookings/{booking_id}/rebook": (
        "/admin/groups/demo/agenda/bookings/{id}/rebook"
    ),
    "/admin/groups/{slug}/bookings/{booking_id}": DETAIL,
    "/admin/groups/{slug}/queue": "/admin/groups/demo/queue",
    "/admin/groups/{slug}/queue/{booking_id}": QUEUE_ITEM,
    "/admin/groups/{slug}/closure": "/admin/groups/demo/closure",
    "/admin/groups/{slug}/closure/contacts": "/admin/groups/demo/closure/contacts",
    "/admin/groups/{slug}/closure/scenarios": "/admin/groups/demo/closure/scenarios",
    "/admin/groups/{slug}/closure/scenarios/{scenario_id}": (
        "/admin/groups/demo/closure/scenarios/{scenario}"
    ),
    "/admin/groups/{slug}/closure/scenarios/{scenario_id}/contacts": (
        "/admin/groups/demo/closure/scenarios/{scenario}/contacts"
    ),
    "/admin/groups/{slug}/closure/scenarios/{scenario_id}/image": (
        "/admin/groups/demo/closure/scenarios/{scenario}/image"
    ),
    "/admin/groups/{slug}/closure/bookings/{booking_id}/move": (
        "/admin/groups/demo/closure/bookings/{on_hold}/move"
    ),
    "/admin/organisations/{org}": "/admin/organisations/demo",
    "/admin/organisations/{org}/settings": "/admin/organisations/demo/settings",
    "/admin/organisations/{org}/branding": "/admin/organisations/demo/branding",
    "/admin/organisations/{org}/branding/theme.css": "/admin/organisations/demo/branding/theme.css",
    "/admin/organisations/{org}/channels": "/admin/organisations/demo/channels",
    "/admin/organisations/{org}/holidays": "/admin/organisations/demo/holidays",
    "/admin/organisations/{org}/closure": "/admin/organisations/demo/closure",
    "/admin/organisations/{org}/contacts": "/admin/organisations/demo/contacts",
    "/admin/organisations/{org}/embedding": "/admin/organisations/demo/embedding",
    "/admin/organisations/{org}/closure/scenarios": "/admin/organisations/demo/closure/scenarios",
    "/admin/organisations/{org}/closure/scenarios/{scenario_id}": (
        "/admin/organisations/demo/closure/scenarios/{org_scenario}"
    ),
    "/admin/organisations/{org}/closure/scenarios/{scenario_id}/contacts": (
        "/admin/organisations/demo/closure/scenarios/{org_scenario}/contacts"
    ),
    "/admin/organisations/{org}/closure/scenarios/{scenario_id}/image": (
        "/admin/organisations/demo/closure/scenarios/{org_scenario}/image"
    ),
    "/admin/roles/{role_id}": "/admin/roles/{role}",
    "/admin/staff/{member_id}": "/admin/staff/{member}",
    "/admin/help/{topic}": "/admin/help/advanced-settings",
    "/admin/holidays/{calendar}": "/admin/holidays/nationaal",
    "/admin/api/clients/{client_id}": "/admin/api/clients/{client}",
}
"""Concrete path per parametrised admin GET route; the placeholders in braces are filled
from ``Fixture``."""


class Fixture:
    """What the sweep put in the database, by id, and which templates were rendered."""

    def __init__(self) -> None:
        self.rendered: set[str] = set()
        self.booking_id = 0
        self.queued_id = 0
        self.on_hold_id = 0
        self.rule_id = 0
        self.scenario_id = 0
        self.org_scenario_id = 0
        self.role_id = 0
        self.member_id = 0
        self.client_id = ""

    def fill(self, path: str) -> str:
        """Return ``path`` with the fixture's ids filled in."""
        values = {
            "{id}": self.booking_id,
            "{queued}": self.queued_id,
            "{on_hold}": self.on_hold_id,
            "{rule}": self.rule_id,
            "{scenario}": self.scenario_id,
            "{org_scenario}": self.org_scenario_id,
            "{role}": self.role_id,
            "{member}": self.member_id,
            "{client}": self.client_id,
        }
        for placeholder, value in values.items():
            path = path.replace(placeholder, str(value))
        return path


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The demo site running release 1.3.0, so the update banner (ADR 0029) is rendered."""
    return settings.model_copy(update={"app_version": "1.3.0"})


def _uow(site: Site) -> SqlUnitOfWork:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    return SqlUnitOfWork(app.state.engine)


def _app(site: Site) -> object:
    return site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001


async def _post(site: Site, path: str, data: dict[str, str]) -> httpx.Response:
    return await site.client.post(path, data=data, headers=SAME_ORIGIN)


def _created(response: httpx.Response, pattern: str) -> int:
    """The id of what a form just created, from the redirect."""
    assert response.status_code == 303, response.text
    match = re.search(pattern, response.headers["location"])
    assert match is not None, response.headers["location"]
    return int(match.group(1))


async def _first_id(site: Site, page: str, pattern: str) -> int:
    """The first id linked from a list page."""
    match = re.search(pattern, (await site.client.get(page)).text)
    assert match is not None, f"{page} links nothing matching {pattern}"
    return int(match.group(1))


def _record_templates(monkeypatch: pytest.MonkeyPatch, into: set[str]) -> None:
    """Record every template Jinja is asked for (a page, a partial, a layout) into ``into``.

    ``get_template`` is the one door: ``extends``, ``include`` and ``import`` go through it
    too, and it is called whether or not the environment has the template cached (the admin
    environment is module-level, so earlier tests in the process may have warmed it).
    """
    original = Environment.get_template

    def recording(
        self: Environment,
        name: str | Template,
        parent: str | None = None,
        globals: MutableMapping[str, Any] | None = None,  # noqa: A002 - Jinja's own name
    ) -> Template:
        if isinstance(name, str):
            into.add(name)
        return original(self, name, parent, globals)

    monkeypatch.setattr(Environment, "get_template", recording)


@pytest.fixture
async def marked(site: Site, monkeypatch: pytest.MonkeyPatch) -> tuple[Site, Fixture]:
    """The site with marker bookings in the demo group, and what else the routes need.

    The demo group gets a free-text field and the own queue (ADR 0043), next week's slot a
    staffing shortfall (the planner's slot pages, the shortfall alert), and the clock ends
    five minutes before the queued booking, so the agenda shows it today and the superuser
    can pick it up.
    """
    uow = _uow(site)
    found = Fixture()
    _record_templates(monkeypatch, found.rendered)
    async with uow.locked() as tx:
        summary = await tx.get_group_summary("demo")
        assert summary is not None
        group_id = summary.group.id or 0
        await tx.save_group(replace(summary.group, dispatch_kind=DispatchKind.OPENPLANNER_QUEUE))
        form = await tx.get_form(group_id)
        note = FieldDefinition.create(
            key="toelichting", type=FieldType.LONG_TEXT, labels={"nl": "Toelichting", "en": "Note"}
        )
        await tx.save_form(group_id, FormDefinition(form.version + 1, (*form.fields, note)))
        await tx.save_release_check(ReleaseCheck(NOW, RELEASE))
    service = BookingService(uow, site.clock, TEST_TOKEN_KEY)

    async def book(number: int, start: datetime) -> int:
        receipt = await book_confirmed(
            service, group_slug="demo", start=start, contact=citizen(number), locale="nl"
        )
        return receipt.booking.id or 0

    found.queued_id = await book(1, TODAY_0900)
    found.booking_id = await book(2, NEXT_WEEK_0900)
    await book(3, NEXT_WEEK_0900)  # two in one slot, one agent next week: a shortfall
    waiting_id = await book(4, NEXT_WEEK_0930)
    found.on_hold_id = await book(5, CLOSED_DAY_0900)
    planning = PlanningService(uow, site.clock, AdminService(uow, site.clock))
    one_agent = ScheduleOverride(NEXT_WEEK_0900.date(), TimeWindow(time(9), time(10), 1))
    await planning.add_override(SUPER, "demo", one_agent)
    async with uow.locked() as tx:
        await tx.save_meeting(found.queued_id, MeetingRef("m-zq", MARKERS["guest_join_url"]))
        trial = TrialCall(MeetingRef("t-zq", MARKERS["trial_join_url"]), site.clock.now())
        await tx.save_trial_call(found.queued_id, trial)  # ADR 0071: no admin page shows it
        waiting = await tx.get_booking(waiting_id)
        assert waiting is not None
        await tx.save_booking(waiting.request_rebook(ChangeReason.STAFFING, site.clock.now()))

    await site.login_as(GROUP_ADMIN)  # a staff record; the superuser is never listed
    await site.login_as(SUPER)
    found.member_id = await _first_id(site, "/admin/staff", r"/admin/staff/(\d+)")
    found.rule_id = await _first_id(
        site, "/admin/groups/demo/staffing", r"/admin/groups/demo/staffing/(\d+)"
    )
    found.role_id = _created(await _post(site, "/admin/roles", ROLE), r"/admin/roles/(\d+)")
    found.scenario_id = _created(
        await _post(site, "/admin/groups/demo/closure/scenarios", SCENARIO),
        r"/admin/groups/demo/closure/scenarios/(\d+)",
    )
    found.org_scenario_id = _created(
        await _post(site, "/admin/organisations/demo/closure/scenarios", SCENARIO),
        r"/admin/organisations/demo/closure/scenarios/(\d+)",
    )
    closed = await _post(site, "/admin/groups/demo/closure", CLOSURE)
    assert closed.status_code == 303, closed.text
    secret_page = await _post(site, "/admin/api/clients", CLIENT)  # renders api_secret.html
    assert secret_page.status_code == 200, secret_page.text
    client_id = re.search(r"opc_[A-Za-z0-9_-]+", secret_page.text)
    assert client_id is not None
    found.client_id = client_id.group(0)

    site.clock.advance(TODAY_0900 - site.clock.now() - timedelta(minutes=5))
    await site.login_as(SUPER)  # the session of yesterday has expired
    picked = await _post(site, "/admin/groups/demo/queue/next", {})
    assert picked.status_code == 303, picked.text
    assert picked.headers["location"] == found.fill(QUEUE_ITEM)
    return site, found


def _get_routes(site: Site) -> list[APIRoute]:
    """Every admin GET route that an admin can open."""
    app = _app(site)
    return [
        route
        for route in all_routes(app.routes)  # type: ignore[attr-defined]
        if "GET" in route.methods
        and route.path.startswith("/admin")
        and route.path not in PUBLIC_ADMIN_PATHS
    ]


def _concrete(route: APIRoute, found: Fixture) -> str:
    """Return the path to fetch for ``route``; a parametrised route must be in ``PATHS``."""
    if "{" not in route.path:
        return route.path
    assert route.path in PATHS, f"add a concrete path for {route.path} to PATHS"
    return found.fill(PATHS[route.path])


def _allowed(path: str, found: Fixture) -> set[str]:
    """The markers ADR 0063 §4 allows on ``path``."""
    if path == found.fill(DETAIL):
        return set(MARKERS.values())
    if path == found.fill(QUEUE_ITEM):
        return {MARKERS["first_name"], MARKERS["guest_join_url"]}
    if path in {found.fill(p) for p in FIRST_NAME_PAGES}:
        return {MARKERS["first_name"]}
    return set()


def _leaks(body: str, allowed: set[str]) -> set[str]:
    return {marker for marker in MARKERS.values() if marker in body and marker not in allowed}


async def test_no_marker_leaves_the_allowed_pages_and_every_template_is_swept(
    marked: tuple[Site, Fixture],
) -> None:
    site, found = marked

    # The pages that may show personal data first: they prove the markers are live, and the
    # audit log then holds a view of the marker booking before the log and its export are swept.
    detail = (await site.client.get(found.fill(DETAIL))).text
    assert MARKERS["form_answers"] in detail, "the detail page does not show the answer"
    queue_item = (await site.client.get(found.fill(QUEUE_ITEM))).text
    assert MARKERS["first_name"] in queue_item
    assert MARKERS["guest_join_url"] in queue_item
    agenda = (await site.client.get("/admin/groups/demo/agenda")).text
    assert MARKERS["first_name"] in agenda, "the agenda does not show today's booking"

    leaks: dict[str, set[str]] = {}
    statuses: dict[str, int] = {}
    for route in sorted(_get_routes(site), key=lambda r: r.path):
        path = _concrete(route, found)
        response = await site.client.get(path)
        statuses[path] = response.status_code
        if found_leaks := _leaks(response.text, _allowed(path, found)):
            leaks[path] = found_leaks
    assert leaks == {}, "personal data on a list, overview, export or alert page (ADR 0063 §2)"
    # An image route has none to show; the certificate request download (ADR 0070) has no
    # request waiting. Both answer 404 on purpose and carry no personal data.
    no_content = ("/image", "/tls/csr.pem")
    not_reached = {p: s for p, s in statuses.items() if s >= 400 and not p.endswith(no_content)}
    assert not_reached == {}, "a swept page did not render; fix its path or the fixture"

    # The chart views of the overview widgets (ADR 0061 §5) are other templates on the same
    # routes, reached only by asking for them: counts and group names, never a marker.
    for item in WIDGETS:
        if not (item.available and item.key in CHART_SERIES):
            continue
        for chart_view in (v for v in item.views if v in KIND_OF_VIEW):
            for path in (
                f"{item.path}?view={chart_view.value}",
                f"{OVERVIEW_PATH}?{item.key}={chart_view.value}",
            ):
                response = await site.client.get(path)
                assert response.status_code == 200, path
                assert "<svg" in response.text or "widget__empty" in response.text, path
                assert not _leaks(response.text, set()), path

    # The pages outside the happy path: a refusal, and the sign-out pages without a session.
    for path, response in (
        ("/admin/help/nope", await site.client.get("/admin/help/nope")),
        ("POST /admin/logout", await _post(site, "/admin/logout", {})),
        ("/admin/logged-out", await site.client.get("/admin/logged-out")),
    ):
        assert not _leaks(response.text, set()), path

    never = sorted(TEMPLATE_NAMES - found.rendered)
    assert never == [], f"templates never rendered with the marker bookings: {never}"


def _query_names(dependant: Dependant) -> set[str]:
    """Every query parameter of a route, through its dependencies."""
    names = {p.name for p in dependant.query_params}
    for inner in dependant.dependencies:
        names |= _query_names(inner)
    return names


def test_no_list_filter_or_query_parameter_names_a_pii_field(site: Site) -> None:
    """ADR 0063 §3: a filter on a personal-data field would let a result say who someone is."""
    forbidden = PII_FIELDS | ALIASES | {"q", "search", "query"}
    for route in _get_routes(site):
        names = _query_names(route.dependant)
        assert not names & forbidden, route.path
    for narrow in (AuditFilter, StaffFilter):
        assert not {f.name for f in fields(narrow)} & forbidden, narrow.__name__


async def test_a_pii_value_in_any_query_parameter_finds_nothing_and_is_not_reflected(
    marked: tuple[Site, Fixture],
) -> None:
    """Abuse case "PII through list search or export" (docs/threat-model.md)."""
    site, found = marked
    for route in _get_routes(site):
        path = _concrete(route, found)
        if path in {found.fill(DETAIL), found.fill(QUEUE_ITEM)}:
            continue
        names = _query_names(route.dependant) | {"q", "search", "email", "name"}
        params = {name: MARKERS["email"] for name in names}
        response = await site.client.get(path, params=params)
        assert response.status_code < 500, path
        assert MARKERS["email"] not in response.text, path
        assert not _leaks(response.text, _allowed(path, found)), path
