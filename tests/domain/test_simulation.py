"""SimulationService: stand-in platform ids so the simulator can play events (not prod)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef, TrialCall
from app.domain.calls import EventKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.integration import IntegrationService
from app.domain.scheduling import Group
from app.domain.simulation import SIMULATED_LINK, SimulationService
from app.domain.trial_call import TrialState, trial_state
from tests.domain.test_integration import _booking
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = two_organisations()
    state.bookings[123] = _booking()  # group 1 = demo
    return InMemoryUnitOfWork(state)


def _service(uow: InMemoryUnitOfWork, *, enabled: bool = True) -> SimulationService:
    clock = FixedClock(NOW)
    admin = AdminService(uow, clock)
    integration = IntegrationService(uow, clock, admin)
    return SimulationService(uow, clock, admin, integration, enabled=enabled)


async def test_prepare_gives_a_booking_stand_in_ids_and_audits_it(uow: InMemoryUnitOfWork) -> None:
    ids = await _service(uow).prepare(ORG_ADMIN, "demo", 123)

    booking = uow.state.bookings[123]
    assert ids.meeting_id == "simulated-meeting-123"
    assert ids.task_id == "simulated-task-123"
    assert booking.meeting == MeetingRef("simulated-meeting-123", SIMULATED_LINK)
    assert booking.wxcc_task_id == "simulated-task-123"
    timeline = [(e.kind, e.detail) for e in uow.state.booking_events[123]]
    assert timeline == [
        (EventKind.MEETING_CREATED, "simulated"),
        (EventKind.TASK_CREATED, "simulated"),
    ]
    assert SIMULATED_LINK.startswith("https://")
    assert ".invalid/" in SIMULATED_LINK
    event = uow.state.audit[-1]
    assert (event.action, event.reason, event.group) == ("simulator.prepare", "", "demo")


async def test_real_platform_ids_are_never_overwritten(uow: InMemoryUnitOfWork) -> None:
    real = MeetingRef("real-meeting", "https://site.webex.com/j.php?MTID=m1")
    uow.state.bookings[123] = replace(_booking(), meeting=real, wxcc_task_id="real-task")

    ids = await _service(uow).prepare(ORG_ADMIN, "demo", 123)

    assert (ids.meeting_id, ids.task_id) == ("real-meeting", "real-task")
    assert uow.state.bookings[123].meeting == real
    assert uow.state.booking_events.get(123, []) == []  # nothing invented for real ids


async def test_ids_reads_what_the_booking_has(uow: InMemoryUnitOfWork) -> None:
    service = _service(uow)
    before = await service.ids(ORG_ADMIN, "demo", 123)
    await service.prepare(ORG_ADMIN, "demo", 123)
    after = await service.ids(ORG_ADMIN, "demo", 123)

    assert (before.meeting_id, before.task_id) == (None, None)
    assert after.meeting_id == "simulated-meeting-123"


async def test_a_booking_of_another_group_is_not_found(uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as error:
        await _service(uow).prepare(SUPER, "andere-team", 123)
    assert error.value.code is ErrorCode.BOOKING_NOT_FOUND


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OUTSIDER])
async def test_only_group_editors_may_simulate(uow: InMemoryUnitOfWork, principal: object) -> None:
    with pytest.raises(DomainError) as error:
        await _service(uow).prepare(principal, "demo", 123)  # type: ignore[arg-type]
    assert error.value.code is ErrorCode.NOT_PERMITTED


async def test_switched_off_the_simulator_refuses_everything(uow: InMemoryUnitOfWork) -> None:
    service = _service(uow, enabled=False)
    for call in (service.prepare, service.ids, service.record_event):
        with pytest.raises(DomainError) as error:
            await call(ORG_ADMIN, "demo", 123)  # type: ignore[call-arg]
        assert error.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.bookings[123].meeting is None


async def test_each_played_event_is_audited(uow: InMemoryUnitOfWork) -> None:
    await _service(uow).record_event(ORG_ADMIN, "demo", 123, "host_joined")

    event = uow.state.audit[-1]
    assert (event.action, event.reason, event.group) == ("simulator.event", "host_joined", "demo")


def _with_video_host(uow: InMemoryUnitOfWork) -> Group:
    """Give the demo group a video host, so it offers video and the trial call (ADR 0071)."""
    group = replace(uow.state.groups[1], video_host_email="host@example.nl")
    uow.state.groups[1] = group
    return group


async def test_prepare_opens_a_stand_in_trial_call_where_the_group_offers_one(
    uow: InMemoryUnitOfWork,
) -> None:
    group = _with_video_host(uow)

    ids = await _service(uow).prepare(ORG_ADMIN, "demo", 123)

    booking = uow.state.bookings[123]
    expected = TrialCall(
        MeetingRef("simulated-trial-123", SIMULATED_LINK),
        NOW + timedelta(minutes=group.trial_call_minutes),
    )
    assert ids.trial_meeting_id == "simulated-trial-123"
    assert booking.trial == expected
    assert booking.trial_count == 1  # it counts, as a real one would
    assert trial_state(booking, group, NOW) is TrialState.ACTIVE
    assert uow.state.booking_events[123][-1].kind is EventKind.TRIAL_CREATED
    assert uow.state.booking_events[123][-1].detail == "simulated"


async def test_without_a_trial_call_on_offer_prepare_opens_none(uow: InMemoryUnitOfWork) -> None:
    ids = await _service(uow).prepare(ORG_ADMIN, "demo", 123)  # no video host: no trial call

    assert ids.trial_meeting_id is None
    assert uow.state.bookings[123].trial is None
    kinds = [e.kind for e in uow.state.booking_events[123]]
    assert EventKind.TRIAL_CREATED not in kinds


async def test_an_open_trial_call_is_never_replaced(uow: InMemoryUnitOfWork) -> None:
    _with_video_host(uow)
    real = TrialCall(
        MeetingRef("real-trial", "https://site.webex.com/j.php?MTID=t1"),
        NOW + timedelta(minutes=20),
    )
    uow.state.bookings[123] = replace(_booking(), trial=real, trial_count=1)

    ids = await _service(uow).prepare(ORG_ADMIN, "demo", 123)

    assert ids.trial_meeting_id == "real-trial"
    assert uow.state.bookings[123].trial == real
    assert uow.state.bookings[123].trial_count == 1
    assert (await _service(uow).ids(ORG_ADMIN, "demo", 123)).trial_meeting_id == "real-trial"
