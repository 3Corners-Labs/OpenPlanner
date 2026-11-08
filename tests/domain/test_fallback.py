"""The fallback of a contact-center group to its own queue (ADR 0082 points 11 to 13)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.activity_alert import CHANGES_ALERT
from app.domain.admin import AdminService
from app.domain.alerts import FALLBACK_EVENT
from app.domain.audit import Outcome
from app.domain.authorization import Scope
from app.domain.booking import Booking
from app.domain.calls import EventKind
from app.domain.connection_health import HealthEntry, HealthState, Platform
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.errors import DomainError, ErrorCode
from app.domain.fallback import CONTACT_CENTER_DOWN, DISPATCH_FAILED, FallbackService
from app.domain.job_plan import JobType, PlannedJob
from app.domain.ports import IntegrationError
from app.domain.queue_service import load_entries
from app.domain.scheduling import DispatchFallback, Group
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from tests.fakes.integrations import webex_cc
from tests.fakes.jobs import FakeQueue, job
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER
from tests.support.call_jobs import WIRED, Setup, group_uow
from tests.support.domain import NOW, TEST_TOKEN_KEY

ROOM = "Y2lzY29zcGFyazovL3VzL1JPT00v"


class Poster:
    def __init__(self) -> None:
        self.posts: list[tuple[int, str, str]] = []
        self.error: IntegrationError | None = None

    async def post(self, organisation_id: int, room_id: str, markdown: str) -> None:
        if self.error:
            raise self.error
        self.posts.append((organisation_id, room_id, markdown))


class Texts:
    def activated(self, group: Group, locale: str) -> str:
        return f"on:{group.slug}:{locale}"

    def queued(self, group: Group, booking: Booking, locale: str) -> str:
        first = booking.contact.first_name if booking.contact else "-"
        return f"queued:{first}"


def wired(**fields: object) -> Setup:
    values: dict[str, object] = {
        **WIRED,
        "dispatch_fallback": DispatchFallback.OWN_QUEUE,
        "fallback_space_room_id": ROOM,
        **fields,
    }
    setup = Setup(group_uow(**values))
    setup.uow.state.organisation_slugs = {1: "demo"}
    return setup


def service(
    setup: Setup, poster: Poster | None = None, alert_per_hour: int = 50
) -> FallbackService:
    return FallbackService(
        setup.uow,
        setup.clock,
        AdminService(setup.uow, setup.clock),
        setup.integration,
        poster or Poster(),
        Texts(),
        alert_per_hour,
    )


def redispatched(setup: Setup, booking_id: int) -> list[PlannedJob]:
    """The dispatch jobs planned now for the booking (the fallback's end plans them)."""
    return [
        j
        for j in setup.uow.state.jobs.get(booking_id, [])
        if j.type is JobType.DISPATCH_CONTACT_CENTER_TASK and j.run_at == setup.clock.now()
    ]


def health(state: HealthState, org: str = "demo") -> list[HealthEntry]:
    return [HealthEntry(org, Platform.WXCC, state, NOW)]


async def test_the_group_falls_back_while_the_contact_center_is_down_and_returns() -> None:
    setup, poster = wired(), Poster()
    fallback = service(setup, poster)
    bid = await setup.booking()
    assert await fallback.sweep(health(HealthState.DEGRADED)) == []
    assert await fallback.sweep(health(HealthState.DOWN)) == ["aangifte"]
    assert setup.uow.state.groups[1].fallback_active
    assert await fallback.sweep(health(HealthState.DOWN)) == []  # already on
    assert poster.posts == [(1, ROOM, "on:aangifte:nl")]
    group = setup.uow.state.groups[1]
    booking = setup.uow.state.bookings[bid]
    assert await fallback.diverts(booking, group)
    assert setup.uow.state.call_status[bid].fallback
    assert poster.posts[-1][2] == "queued:Ada"
    assert await fallback.sweep(health(HealthState.OK)) == ["aangifte"]
    assert not setup.uow.state.groups[1].fallback_active
    assert not setup.uow.state.call_status[bid].fallback  # back to the contact center
    # A fresh dispatch job: its retries and a final failure (back to the queue) still apply.
    assert [(j.run_at, j.pinned_start) for j in redispatched(setup, bid)] == [(NOW, booking.start)]
    events = [e for e in setup.uow.state.audit if e.action == FALLBACK_EVENT]
    assert [(e.outcome, e.reason, e.group) for e in events] == [
        (Outcome.FAILURE, "on", "aangifte"),
        (Outcome.SUCCESS, "", "aangifte"),
    ]


async def test_a_group_without_the_fallback_or_on_its_own_queue_never_switches() -> None:
    setup = wired(dispatch_fallback=DispatchFallback.NONE)
    assert await service(setup).sweep(health(HealthState.DOWN)) == []


async def test_a_claimed_booking_stays_in_the_queue_when_the_fallback_ends() -> None:
    from app.domain.booking import Claim

    setup = wired()
    fallback = service(setup)
    bid = await setup.booking()
    await fallback.sweep(health(HealthState.DOWN))
    assert await fallback.diverts(setup.uow.state.bookings[bid], setup.uow.state.groups[1])
    setup.uow.state.bookings[bid] = replace(
        setup.uow.state.bookings[bid], claim=Claim(NOW, "agent")
    )
    await fallback.sweep(health(HealthState.OK))
    assert redispatched(setup, bid) == []
    assert setup.uow.state.call_status[bid].fallback


async def test_a_failing_space_never_stops_the_fallback() -> None:
    setup, poster = wired(), Poster()
    poster.error = IntegrationError("unauthorized", retryable=False)
    assert await service(setup, poster).sweep(health(HealthState.DOWN)) == ["aangifte"]


async def test_ending_by_hand_needs_bookings_manage_and_is_audited() -> None:
    from tests.support.admin import ORG_ADMIN

    setup = wired()
    setup.uow.state.organisations = {}
    fallback = service(setup)
    await fallback.sweep(health(HealthState.DOWN))
    with pytest.raises(DomainError) as refused:
        await fallback.end(OUTSIDER, "aangifte")
    assert refused.value.code is ErrorCode.NOT_PERMITTED
    from app.domain.authorization import Role
    from tests.support.principals import principal

    planner = principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
    assert await fallback.end(planner, "aangifte")
    assert not await fallback.end(planner, "aangifte")
    assert any(e.detail == "fallback.off" and e.actor == "o-1" for e in setup.uow.state.audit)
    _ = (GROUP_ADMIN, ORG_ADMIN)


async def test_ending_by_hand_counts_toward_the_alert_on_many_changes_by_one_person() -> None:
    setup = wired()
    fallback = service(setup, alert_per_hour=0)
    await fallback.sweep(health(HealthState.DOWN))

    assert await fallback.end(ORG_ADMIN, setup.uow.state.groups[1].slug)

    alerts = [e for e in setup.uow.state.audit if e.action == CHANGES_ALERT.action]
    assert [e.detail for e in alerts] == ["actor:o-1,changes:1"]


def runner(setup: Setup, queue: FakeQueue, fallback: FallbackService) -> JobRunner:
    composer = MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x")
    return JobRunner(
        queue,
        setup.uow,
        setup.clock,
        None,
        composer,
        video=setup.video,
        dispatchers=webex_cc(setup.dispatcher),
        integration=setup.integration,
        fallback=fallback,
    )


async def test_a_dispatch_job_skips_while_the_group_is_in_fallback() -> None:
    setup = wired()
    fallback = service(setup)
    bid = await setup.booking()
    await fallback.sweep(health(HealthState.DOWN))
    queue = FakeQueue([job(bid, JobType.DISPATCH_CONTACT_CENTER_TASK)])
    await runner(setup, queue, fallback).run_due()
    assert setup.dispatcher.items == []
    assert queue.failed == []
    events = setup.uow.state.booking_events[bid]
    assert (events[-1].kind, events[-1].detail) == (EventKind.FALLBACK_QUEUED, CONTACT_CENTER_DOWN)


async def test_a_dispatch_that_fails_for_good_lands_in_the_queue() -> None:
    setup = wired()
    bid = await setup.booking()
    setup.dispatcher.error = IntegrationError("server_error", retryable=True)
    queue = FakeQueue([job(bid, JobType.DISPATCH_CONTACT_CENTER_TASK, attempts=4)])
    setup.uow.state.bookings[bid] = replace(setup.uow.state.bookings[bid], meeting=None)
    from app.domain.booking import MeetingRef

    setup.uow.state.bookings[bid] = replace(
        setup.uow.state.bookings[bid], meeting=MeetingRef("m", "https://x.webex.com/m")
    )
    await runner(setup, queue, service(setup)).run_due()
    events = setup.uow.state.booking_events[bid]
    assert (events[-1].kind, events[-1].detail) == (EventKind.FALLBACK_QUEUED, DISPATCH_FAILED)
    assert setup.uow.state.call_status[bid].fallback


async def test_a_fallback_queue_shows_only_the_bookings_that_fell_back() -> None:
    setup = wired()
    first = await setup.booking()
    group = setup.uow.state.groups[1]
    assert group.queue_open
    near = NOW.replace(hour=7, minute=55) + timedelta(days=4)  # Monday 08:55, before 09:00
    async with setup.uow.read() as tx:
        before = await load_entries(tx, Scope(everything=True), group, near)
    assert before == []
    await service(setup).dispatch_failed(first)
    async with setup.uow.read() as tx:
        after = await load_entries(tx, Scope(everything=True), group, near)
    assert [e.booking.id for e in after] == [first]


async def test_a_group_not_in_fallback_or_a_dispatched_booking_is_not_diverted() -> None:
    setup = wired()
    fallback = service(setup)
    bid = await setup.booking()
    booking, group = setup.uow.state.bookings[bid], setup.uow.state.groups[1]
    assert not await fallback.diverts(booking, group)
    await fallback.sweep(health(HealthState.DOWN))
    group = setup.uow.state.groups[1]
    assert not await fallback.diverts(
        replace(
            booking, contact_center_task=ContactCenterTaskRef("t-1", ContactCenterKind.WEBEX_CC)
        ),
        group,
    )


async def test_a_dispatch_failure_of_a_group_without_the_fallback_stays_out_of_the_queue() -> None:
    setup = wired(dispatch_fallback=DispatchFallback.NONE)
    bid = await setup.booking()
    await service(setup).dispatch_failed(bid)
    assert bid not in setup.uow.state.call_status or not setup.uow.state.call_status[bid].fallback


async def test_a_direct_call_in_fallback_reaches_the_queue_with_a_line_in_the_space() -> None:
    from app.domain.call_service import CallService
    from app.web.call_labels import agent_start_label

    setup, poster = wired(), Poster()
    fallback = service(setup, poster)
    bid = await setup.booking()
    await fallback.sweep(health(HealthState.DOWN))
    calls = CallService(
        setup.uow,
        setup.integration,
        setup.video,
        webex_cc(setup.dispatcher),
        agent_start_label,
        fallback,
    )
    await calls.ensure_task(setup.uow.state.bookings[bid], setup.uow.state.groups[1])
    assert setup.dispatcher.items == []
    assert setup.uow.state.call_status[bid].fallback
    assert poster.posts[-1][2] == "queued:Ada"
