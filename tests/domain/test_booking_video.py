"""Which booking gets a meeting, and what its pages offer (ADR 0121 row 36).

``gets_meeting`` is the one answer for the worker, the retry, the message and the participant's
pages: the service has a video host and the booking is a video call by its flow.
"""

from dataclasses import replace

from app.domain.booking_video import flow_has_video, gets_meeting
from app.domain.flow_resolution import FlowRef
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import ADA, SLOT_0900, book_confirmed
from tests.support.request_work import FLOW, VIDEO_FLOW, booking_service, world

HOST = "host@voorbeeld.nl"


async def _booked(uow: InMemoryUnitOfWork, clock: FixedClock, flow: FlowRef | None) -> str:
    """Book at 09:00 under ``flow``; return the management token."""
    who = replace(ADA, email=f"p{len(uow.state.bookings)}@voorbeeld.nl")
    receipt = await book_confirmed(
        booking_service(uow, clock), group_slug="demo", start=SLOT_0900, contact=who, locale="nl"
    )
    booking_id = receipt.booking.id or 0
    uow.state.bookings[booking_id] = replace(uow.state.bookings[booking_id], flow=flow)
    return receipt.management_token


async def test_a_booking_gets_a_meeting_when_its_flow_is_a_video_call_and_a_host_exists(
    clock: FixedClock,
) -> None:
    uow = world()
    service = booking_service(uow, clock)
    tokens = {flow: await _booked(uow, clock, flow) for flow in (None, VIDEO_FLOW, FLOW)}

    without_host = {flow: (await service.find(t)).video for flow, t in tokens.items()}
    uow.state.groups[1] = replace(uow.state.groups[1], video_host_email=HOST)
    with_host = {flow: (await service.find(t)).video for flow, t in tokens.items()}

    assert without_host == {None: False, VIDEO_FLOW: False, FLOW: False}
    assert with_host == {None: True, VIDEO_FLOW: True, FLOW: False}


async def test_the_rule_reads_the_bookings_flow_revision(clock: FixedClock) -> None:
    uow = world()
    uow.state.groups[1] = replace(uow.state.groups[1], video_host_email=HOST)
    await _booked(uow, clock, FLOW)
    booking = next(iter(uow.state.bookings.values()))
    group = uow.state.groups[1]

    async with uow.read() as tx:
        assert not await flow_has_video(tx, booking)
        assert not await gets_meeting(tx, booking, group)
        assert await gets_meeting(tx, replace(booking, flow=VIDEO_FLOW), group)
        assert await gets_meeting(tx, replace(booking, flow=None), group)
