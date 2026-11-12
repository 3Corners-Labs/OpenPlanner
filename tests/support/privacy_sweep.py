"""The marker site of the privacy sweeps (ADR 0063): bookings whose personal data are markers.

``mark`` fills a running demo site with bookings whose personal-data fields hold the unique
values of ``MARKERS``, and with what else the admin routes need to render: a role, closure
scenarios, a machine client, an organisation provider and a log destination. ``marker_leaks``
finds a marker in a page. The site runs ``RELEASED`` while ``RELEASE`` is known, so every page
carries the update banner (ADR 0029). The fixture ``marked`` (``tests/admin/conftest.py``) runs
such a site.
"""

import re
from collections.abc import MutableMapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from jinja2 import Environment, Template

from app.domain.admin import AdminService
from app.domain.authorization import MATRIX, Role, Scope
from app.domain.booking import ChangeReason, MeetingRef, TrialCall
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.form_fields import FieldDefinition, FieldType, FormDefinition
from app.domain.planning import PlanningService
from app.domain.routing import GroupRouting, Subject
from app.domain.scheduling import DispatchKind, ScheduleOverride, ScheduleRule, TimeWindow
from app.domain.service_flows import FlowState, ServiceFlow
from app.domain.services import BookingService, ContactInput
from app.domain.skills import Skill
from app.domain.staff_directory import DirectoryUser
from app.domain.updates import Release, ReleaseCheck, Version
from tests.admin.conftest import GROUP_ADMIN, SUPER, Site
from tests.fakes.identity import FakeIdentityProvider
from tests.fakes.staff_directory import FakeStaffDirectory
from tests.support.admin_site import post_form, site_app, site_uow
from tests.support.domain import FRIDAY_0900, NOW, TEST_TOKEN_KEY, book_confirmed
from tests.support.organisation_providers import NEW_PROVIDER, organisation_claims
from tests.support.service_flows import revision

# The demo group is open on weekdays; the clock starts on Thursday 1 January 2026.
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


def _participant(number: int) -> ContactInput:
    """Return the marker participant: a different address per booking, each with the marker."""
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
"""A custom role with the group admin's rights, for the role editor's pages."""
SCENARIO = {
    "scenario_name": "Storing",
    "notice_nl": "Door een storing zijn wij gesloten.",
    "notice_en": "We are closed because of an outage.",
}
"""A closure scenario, stored for the group and for the organisation."""
CLIENT = {
    "name": "WxCC AI Agent",
    "organisation": "demo",
    "lifetime_days": "90",
    "scope.availability:read": "on",
    "group.demo": "on",
}
"""A machine client of the demo group; the page after creating it shows its secret once."""
CLOSURE = {
    "starts_at": "2026-01-16",
    "starts_at_time": "00:00",
    "ends_at": "2026-01-17",
    "ends_at_time": "00:00",
    "notice_nl": "Door een storing zijn wij gesloten.",
    "notice_en": "We are closed because of an outage.",
}
"""A closure of the demo group on Friday 16 January: the booking of that day is put on hold."""

RELEASED = "1.3.0"
"""The release the marker site runs."""
RELEASE = Release(Version(1, 4, 0), "https://github.com/acme/openplanner/releases/tag/v1.4.0")
"""A newer release than ``RELEASED``, so every page carries the update banner (ADR 0029)."""

QUEUE_ITEM = "/admin/groups/demo/queue/{queued}"
"""The queued booking's page; the superuser picks it from the queue."""
LOG_DESTINATION = {
    "name": "Splunk",
    "kind": "splunk_hec",
    "address": "https://splunk.example.org:8088",
    "secret": "hec-token-0123456789",
    "index": "openplanner",
    "min_level": "info",
    "category_audit": "on",
}
"""A log destination, which holds a write-only credential (ADR 0085 step 4)."""


class Fixture:
    """What the sweep put in the database, by id, and which templates were rendered."""

    def __init__(self) -> None:
        self.rendered: set[str] = set()
        self.booking_id = 0
        self.queued_id = 0
        self.on_hold_id = 0
        self.rule_id = 0
        self.override_id = 0
        self.scenario_id = 0
        self.org_scenario_id = 0
        self.skill_id = 0
        self.pool_id = 0
        self.pool_rule_id = 0
        self.pool_override_id = 0
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
            "{override}": self.override_id,
            "{scenario}": self.scenario_id,
            "{org_scenario}": self.org_scenario_id,
            "{skill}": self.skill_id,
            "{pool_rule}": self.pool_rule_id,
            "{pool_override}": self.pool_override_id,
            "{pool}": self.pool_id,
            "{role}": self.role_id,
            "{member}": self.member_id,
            "{client}": self.client_id,
        }
        for placeholder, value in values.items():
            path = path.replace(placeholder, str(value))
        return path


def marker_leaks(body: str, allowed: set[str]) -> set[str]:
    """Return the markers in ``body`` other than the ``allowed`` ones."""
    return {marker for marker in MARKERS.values() if marker in body and marker not in allowed}


def _created(response: httpx.Response, pattern: str) -> int:
    """Return the id of what a form just created, from the redirect."""
    assert response.status_code == 303, response.text
    match = re.search(pattern, response.headers["location"])
    assert match is not None, response.headers["location"]
    return int(match.group(1))


async def _first_id(site: Site, page: str, pattern: str) -> int:
    """Return the first id that a list page links to."""
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


async def mark(site: Site, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """Store the marker bookings in the demo group, and what else the routes need.

    The demo group gets a free-text field and the own queue (ADR 0043), next week's slot a
    staffing shortfall (the planner's slot pages, the shortfall alert), and the clock ends
    five minutes before the queued booking, so the agenda shows it today and the superuser
    can pick it up.
    """
    uow = site_uow(site)
    found = Fixture()
    _record_templates(monkeypatch, found.rendered)
    # ADR 0095: a staff directory, so the invitation form and the actions on a person render.
    directory = FakeStaffDirectory(
        [
            DirectoryUser(
                "sub-group",
                "g",
                "g@example.test",
                "G",
                "G",
                True,
                frozenset({"openplanner:group_admin:demo/demo"}),
            )
        ]
    )
    site_app(site).state.staff_directory_service._directory = directory  # type: ignore[attr-defined]  # noqa: SLF001
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
    service = BookingService(uow, site.clock, TEST_TOKEN_KEY, treatment_buffer=0)

    async def book(number: int, start: datetime) -> int:
        receipt = await book_confirmed(
            service, group_slug="demo", start=start, contact=_participant(number), locale="nl"
        )
        return receipt.booking.id or 0

    found.queued_id = await book(1, FRIDAY_0900)  # the queue, once the clock is there
    found.booking_id = await book(2, NEXT_WEEK_0900)
    await book(3, NEXT_WEEK_0900)  # two in one slot, one agent next week: a shortfall
    waiting_id = await book(4, NEXT_WEEK_0930)
    found.on_hold_id = await book(5, CLOSED_DAY_0900)
    planning = PlanningService(uow, site.clock, AdminService(uow, site.clock))
    one_agent = ScheduleOverride(NEXT_WEEK_0900.date(), TimeWindow(time(9), time(10), 1))
    found.override_id = await planning.add_override(SUPER, "demo", one_agent)
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
    await post_form(
        site, "/admin/groups/demo/reminders", {"channel": "email", "amount": "3", "unit": "hours"}
    )
    found.rule_id = await _first_id(
        site, "/admin/groups/demo/staffing", r"/admin/groups/demo/staffing/(\d+)"
    )
    found.role_id = _created(await post_form(site, "/admin/roles", ROLE), r"/admin/roles/(\d+)")
    found.scenario_id = _created(
        await post_form(site, "/admin/groups/demo/closure/scenarios", SCENARIO),
        r"/admin/groups/demo/closure/scenarios/(\d+)",
    )
    found.org_scenario_id = _created(
        await post_form(site, "/admin/organisations/demo/closure/scenarios", SCENARIO),
        r"/admin/organisations/demo/closure/scenarios/(\d+)",
    )
    async with uow.locked() as tx:  # ADR 0121: a skill and a counted pool for their pages
        organisation = await tx.get_organisation("demo")
        assert organisation is not None
        skill = await tx.save_skill(
            Scope(everything=True),
            Skill(None, organisation.id, "intake", {"nl": "Intake", "en": "Intake"}),
        )
        pool = await tx.save_capacity_pool(
            Scope(everything=True),
            CapacityPool(
                None,
                organisation.id,
                "Loket",
                PoolMode.COUNTED,
                frozenset({skill.id or 0}),
                code="loket",
            ),
        )
        found.skill_id, found.pool_id = skill.id or 0, pool.id or 0
        everything = Scope(everything=True)  # ADR 0122 step 4: the pool's staffing pages
        await tx.set_pool_timezone(everything, found.pool_id, "Europe/Amsterdam")
        found.pool_rule_id = (
            await tx.add_pool_rule(
                everything, found.pool_id, ScheduleRule(0, TimeWindow(time(9), time(12), 2))
            )
            or 0
        )
        found.pool_override_id = (
            await tx.add_pool_override(
                everything, found.pool_id, ScheduleOverride(date(2026, 1, 12), None)
            )
            or 0
        )
        group = await tx.get_group("demo")  # ADR 0121 step 2d: a draft flow for the flow pages
        assert group is not None
        await tx.save_group_routing(  # ... that offers a subject, for its page of extra skills
            group.id,
            GroupRouting(subjects=(Subject.create("sweep", {"nl": "Sweep", "en": "Sweep"}),)),
        )
        await tx.save_draft(
            Scope(everything=True),
            ServiceFlow(
                None,
                group.id,
                "sweep",
                FlowState.DRAFT,
                frozenset(),
                None,
                revision(
                    flow_id=None,
                    capacity_pool_id=pool.id,
                    subject_skill_ids={"sweep": frozenset()},
                ),
            ),
        )
    closed = await post_form(site, "/admin/groups/demo/closure", CLOSURE)
    assert closed.status_code == 303, closed.text
    secret_page = await post_form(site, "/admin/api/clients", CLIENT)  # renders api_secret.html
    assert secret_page.status_code == 200, secret_page.text
    client_id = re.search(r"opc_[A-Za-z0-9_-]+", secret_page.text)
    assert client_id is not None
    found.client_id = client_id.group(0)
    # ADR 0083 step 2: a registered provider, and its guided test login (the interstitial
    # and the result page are reached through a POST and the callback only).
    registered = await post_form(site, "/admin/organisations/demo/login", NEW_PROVIDER)
    assert registered.status_code == 303, registered.text
    adapter = FakeIdentityProvider()
    adapter.next_claims = organisation_claims(frozenset({"openplanner:org_admin:demo"}))
    site.providers.adapters["org-a"] = adapter
    interstitial = await post_form(site, "/admin/organisations/demo/login/org-a/test", {})
    assert interstitial.status_code == 200, interstitial.text
    verdict = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})
    assert verdict.status_code == 200, verdict.text
    for page in (interstitial, verdict):
        assert not marker_leaks(page.text, set()), "test login pages"

    # ADR 0085 step 4: a log destination, which holds a write-only credential.
    destination = await post_form(site, "/admin/logging", LOG_DESTINATION)
    assert destination.status_code == 303, destination.text
    assert destination.headers["location"].startswith("/admin/logging/1?")

    site.clock.advance(FRIDAY_0900 - site.clock.now() - timedelta(minutes=5))
    await site.login_as(SUPER)  # the session of yesterday has expired
    picked = await post_form(site, "/admin/groups/demo/queue/next", {})
    assert picked.status_code == 303, picked.text
    assert picked.headers["location"] == found.fill(QUEUE_ITEM)
    return found
