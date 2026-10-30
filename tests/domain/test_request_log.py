"""Every request to Webex and WxCC as a system event of the booking (ADR 0068)."""

from datetime import timedelta

from app.domain.audit import Category, Outcome, Source
from app.domain.event_context import bind_booking, bind_level, trace_scope
from app.domain.ports import RequestRecord
from app.domain.request_log import (
    PATH_LIMIT,
    REQUEST_EVENT,
    RequestEvents,
    request_detail,
    request_fields,
)
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.domain.system_log import EventRetention, SystemLog
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.admin import two_organisations
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, window

REFUSED = RequestRecord("POST", "/v1/meetings", 400, "rejected_400", "ROUTER_abc", 231)


async def test_a_request_is_a_system_event_at_the_bookings_level_in_its_trace() -> None:
    uow = InMemoryUnitOfWork(State())
    with trace_scope() as trace_id:
        bind_level("gemeente", "burgerzaken")
        bind_booking(42)
        await RequestEvents(uow, FixedClock(NOW), "0.4.0-rc.7").record(REFUSED)

    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.reason, event.source) == (
        REQUEST_EVENT,
        Outcome.FAILURE,
        "rejected_400",
        Source.SYSTEM,
    )
    assert (event.organisation, event.group, event.booking_id) == ("gemeente", "burgerzaken", 42)
    assert event.event_category is Category.SYSTEM
    assert event.trace_id == trace_id
    assert request_fields(event.detail) == {
        "method": "POST",
        "path": "/v1/meetings",
        "status": "400",
        "ms": "231",
        "tracking": "ROUTER_abc",
        "version": "0.4.0-rc.7",
    }


async def test_a_successful_request_is_a_success() -> None:
    uow = InMemoryUnitOfWork(State())
    ok = RequestRecord("DELETE", "/v1/meetings/abc", 204)

    await RequestEvents(uow, FixedClock(NOW)).record(ok)

    assert (uow.state.audit[-1].outcome, uow.state.audit[-1].reason) == (Outcome.SUCCESS, "")


def test_the_detail_fits_its_column_whatever_the_path() -> None:
    long = RequestRecord("GET", "/v1/" + "x" * 500, 200, tracking_id="t" * 500)

    detail = request_detail(long, "1.2.3-rc.45")

    assert len(detail) <= 255
    assert len(request_fields(detail)["path"]) == PATH_LIMIT


async def test_request_events_are_pruned_on_their_own_retention() -> None:
    uow = InMemoryUnitOfWork(State())
    clock = FixedClock(NOW)
    await RequestEvents(uow, clock).record(REFUSED)
    await SystemLog(uow, clock).record("job.create_video_room", Outcome.FAILURE)
    clock.advance(timedelta(days=31))

    removed = await SystemLog(uow, clock).prune(EventRetention())

    assert removed == 1
    assert [e.action for e in uow.state.audit] == ["job.create_video_room"]


async def test_a_booking_found_through_a_public_page_gets_its_organisation() -> None:
    # A /g/{slug} page binds only the group; the org admin must still see the event.
    state = two_organisations()
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    uow = InMemoryUnitOfWork(state)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY), slug="demo")
    with trace_scope():
        bind_level(None, "demo")
        bind_booking(receipt.booking.id)
        await RequestEvents(uow, FixedClock(NOW)).record(REFUSED)

    event = uow.state.audit[-1]
    assert (event.organisation, event.group) == ("demo", "demo")
