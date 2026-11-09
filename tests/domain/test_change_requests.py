"""A bot's change request named by phone number and appointment code (ADR 0023 step 2).

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; group ``demo`` is staffed on Mondays
09:00-10:00 Amsterdam, so Monday 5 January has 09:00 and 09:30 slots. Every participant of
``book_another`` gives the phone number ``06 1234 5678``.
"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.domain.booking import Booking, BookingStatus
from app.domain.change_request_service import (
    MATCHED,
    NO_MATCH,
    ChangePolicy,
    ChangeRequestService,
)
from app.domain.change_requests import (
    ChangeAction,
    ChangeRequest,
    ChangeStatus,
    appointment_code,
    code_matches,
    derive_code,
    format_code,
    normalise_code,
)
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.job_plan import JobType, is_stale
from app.domain.participant_links import PublicRef
from app.domain.scheduling import GroupSummary
from app.domain.services import BookingService
from app.domain.tokens import derive_token, hash_token
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.agent_api import api_caller
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, book_another

PHONE = "06 1234 5678"
OTHER_KEY = b"o" * 32
CALLER = api_caller()
OTHER_CALLER = api_caller("opc_other")


@pytest.fixture
def policy() -> dict[str, ChangePolicy]:
    """The organisation's settings, changeable per test."""
    return {"value": ChangePolicy(enabled=True, minutes=60)}


@pytest.fixture
def changes(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    bookings: BookingService,
    policy: dict[str, ChangePolicy],
) -> ChangeRequestService:
    return ChangeRequestService(
        uow,
        clock,
        bookings,
        (TEST_TOKEN_KEY,),
        policy=lambda _org: policy["value"],
        phone_region=lambda _org: "NL",
    )


async def _demo(uow: InMemoryUnitOfWork) -> GroupSummary:
    async with uow.read() as tx:
        found = await tx.get_group_summary("demo")
    assert found is not None
    return found


async def _booked(bookings: BookingService, start: Any = SLOT_0900) -> tuple[int, str, str]:
    """Book ``start``; return the booking id, its management token and its appointment code."""
    receipt = await book_another(bookings, start)
    booking = receipt.booking
    return (
        booking.id or 0,
        receipt.management_token,
        derive_code(TEST_TOKEN_KEY, booking.token_salt),
    )


def _jobs(uow: InMemoryUnitOfWork, booking_id: int) -> list[JobType]:
    return [j.type for j in uow.state.jobs[booking_id]]


# --- the code ------------------------------------------------------------------------------


def test_the_code_is_eight_digits_derived_from_the_salt_and_key() -> None:
    code = derive_code(TEST_TOKEN_KEY, "salt")

    assert len(code) == 8
    assert code.isdigit()
    assert derive_code(TEST_TOKEN_KEY, "salt") == code
    assert derive_code(TEST_TOKEN_KEY, "other") != code
    assert derive_code(OTHER_KEY, "salt") != code
    assert code != derive_token(TEST_TOKEN_KEY, "salt")
    with pytest.raises(ValueError, match="at least"):
        derive_code(b"short", "salt")


def test_the_code_follows_the_key_the_token_was_made_with() -> None:
    old_hash = hash_token(derive_token(OTHER_KEY, "salt"))

    booking = cast(Booking, SimpleNamespace(token_salt="salt", token_hash=old_hash))

    assert appointment_code((TEST_TOKEN_KEY, OTHER_KEY), booking) == format_code(
        derive_code(OTHER_KEY, "salt")
    )
    assert appointment_code((TEST_TOKEN_KEY,), booking) == format_code(
        derive_code(TEST_TOKEN_KEY, "salt")
    )
    assert code_matches((TEST_TOKEN_KEY, OTHER_KEY), "salt", derive_code(OTHER_KEY, "salt"))
    assert not code_matches((TEST_TOKEN_KEY,), "salt", derive_code(OTHER_KEY, "salt"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345678", "12345678"),
        ("1234 5678", "12345678"),
        ("1234-5678", "12345678"),
        ("1234.56.78", "12345678"),
        ("1234567", None),
        ("123456789", None),
        ("1234567a", None),
        ("١٢٣٤٥٦٧٨", None),  # digits of another script are no code
    ],
)
def test_a_code_is_read_without_spaces_and_dashes(raw: str, expected: str | None) -> None:
    assert normalise_code(raw) == expected


def test_the_code_is_shown_in_two_halves() -> None:
    assert format_code("12345678") == "1234 5678"


def test_the_status_follows_confirmation_and_expiry() -> None:
    request = ChangeRequest(
        "chr_x",
        "opc_bot",
        1,
        None,
        7,
        SLOT_0900,
        NOW,
        NOW + timedelta(hours=1),
    )

    assert request.status(NOW) is ChangeStatus.PENDING
    assert request.status(NOW + timedelta(hours=1)) is ChangeStatus.EXPIRED
    assert request.confirm(NOW).status(NOW + timedelta(days=1)) is ChangeStatus.CONFIRMED
    assert request.action is ChangeAction.CANCEL
    assert replace(request, target_start=SLOT_0930).action is ChangeAction.RESCHEDULE
    assert request.is_current(SLOT_0900, NOW)
    assert not request.is_current(SLOT_0930, NOW)
    assert not replace(request, booking_id=None).is_current(SLOT_0900, NOW)
    replaced = request.supersede(NOW)
    assert not replaced.is_current(SLOT_0900, NOW)
    assert replaced.status(NOW) is ChangeStatus.PENDING  # the API cannot tell it was replaced


# --- asking -------------------------------------------------------------------------------


async def test_a_matching_pair_asks_the_participant_and_changes_nothing(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, _token, code = await _booked(bookings)

    request = await changes.request(CALLER, await _demo(uow), PHONE, format_code(code), None)

    assert request.booking_id == booking_id
    assert request.status(NOW) is ChangeStatus.PENDING
    assert request.expires_at == NOW + timedelta(minutes=60)
    assert uow.state.bookings[booking_id].status is BookingStatus.CONFIRMED
    sent = [j for j in uow.state.jobs[booking_id] if j.type is JobType.SEND_CHANGE_REQUEST]
    assert {(j.channel, j.pinned_start) for j in sent} == {
        (ChannelKind.EMAIL, SLOT_0900),
        (ChannelKind.SMS, SLOT_0900),  # reachable by SMS, as for reminders
    }
    (event,) = [e for e in uow.state.audit if e.action == "bookings:request_change"]
    assert (event.actor, event.reason, event.detail) == ("opc_bot", MATCHED, "action:cancel")
    assert event.booking_id == booking_id
    assert "612345678" not in repr(event)
    assert code not in repr(event)


async def test_a_wrong_pair_gets_the_same_answer_and_reaches_nobody(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, _token, code = await _booked(bookings)
    wrong = f"{(int(code) + 1) % 10**8:08d}"
    demo = await _demo(uow)

    by_code = await changes.request(CALLER, demo, PHONE, wrong, None)
    by_phone = await changes.request(CALLER, demo, "06 8765 4321", code, None)

    for request in (by_code, by_phone):
        assert request.booking_id is None
        assert request.status(NOW) is ChangeStatus.PENDING
        assert request.expires_at == NOW + timedelta(minutes=60)
    assert JobType.SEND_CHANGE_REQUEST not in _jobs(uow, booking_id)
    reasons = [e.reason for e in uow.state.audit if e.action == "bookings:request_change"]
    assert reasons == [NO_MATCH, NO_MATCH]


async def test_the_code_picks_one_of_several_bookings_with_the_same_number(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    first, _t1, _c1 = await _booked(bookings, SLOT_0900)
    second, _t2, code = await _booked(bookings, SLOT_0930)

    request = await changes.request(CALLER, await _demo(uow), PHONE, code, None)

    assert request.booking_id == second
    assert JobType.SEND_CHANGE_REQUEST not in _jobs(uow, first)


async def test_a_booking_past_its_change_deadline_is_not_matched(
    changes: ChangeRequestService,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    _id, _token, code = await _booked(bookings)
    clock.advance(SLOT_0900 - timedelta(minutes=30) - NOW)  # moving closes 60 minutes before

    demo = await _demo(uow)

    move = await changes.request(CALLER, demo, PHONE, code, SLOT_0930)
    cancel = await changes.request(CALLER, demo, PHONE, code, None)

    assert move.booking_id is None
    assert cancel.booking_id is not None  # cancelling stays open until the start


async def test_refusals_never_depend_on_a_booking(
    changes: ChangeRequestService, uow: InMemoryUnitOfWork, policy: dict[str, ChangePolicy]
) -> None:
    demo = await _demo(uow)

    with pytest.raises(DomainError) as phone:
        await changes.request(CALLER, demo, "12", "12345678", None)
    with pytest.raises(DomainError) as code:
        await changes.request(CALLER, demo, PHONE, "1234", None)
    with pytest.raises(DomainError) as taken:
        await changes.request(
            CALLER,
            demo,
            PHONE,
            "12345678",
            SLOT_0900 + timedelta(minutes=7),
        )
    policy["value"] = ChangePolicy(enabled=False)
    with pytest.raises(DomainError) as off:
        await changes.request(CALLER, demo, PHONE, "12345678", None)

    assert phone.value.code is ErrorCode.INVALID_CONTACT
    assert code.value.code is ErrorCode.INVALID_APPOINTMENT_CODE
    assert taken.value.code is ErrorCode.SLOT_UNAVAILABLE
    assert off.value.code is ErrorCode.CHANGE_REQUESTS_DISABLED
    assert uow.state.change_requests == {}


async def test_a_newer_request_replaces_the_open_one(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    _id, _token, code = await _booked(bookings)
    demo = await _demo(uow)

    first = await changes.request(CALLER, demo, PHONE, code, None)
    second = await changes.request(CALLER, demo, PHONE, code, SLOT_0930)

    old = await changes.status(CALLER, first.public_id)
    assert old.status(NOW) is ChangeStatus.PENDING  # the same answer as an unmatched one
    assert old.expires_at == first.expires_at
    assert not old.is_current(SLOT_0900, NOW)
    new = await changes.status(CALLER, second.public_id)
    assert new.is_current(SLOT_0900, NOW)
    sent = [j for j in uow.state.jobs[_id] if j.type is JobType.SEND_CHANGE_REQUEST]
    assert len(sent) == 2  # one email and one SMS: the first request's were dropped


async def test_only_the_asking_client_reads_the_status(
    changes: ChangeRequestService, uow: InMemoryUnitOfWork
) -> None:
    request = await changes.request(CALLER, await _demo(uow), PHONE, "12345678", None)

    with pytest.raises(DomainError) as other:
        await changes.status(OTHER_CALLER, request.public_id)
    with pytest.raises(DomainError) as unknown:
        await changes.status(CALLER, "chr_unknown")

    assert other.value.code is unknown.value.code is ErrorCode.CHANGE_REQUEST_NOT_FOUND


# --- confirming ---------------------------------------------------------------------------


async def test_the_participant_confirms_a_cancellation(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, token, code = await _booked(bookings)
    request = await changes.request(CALLER, await _demo(uow), PHONE, code, None)
    ref = (await bookings.find(token)).booking.public_ref
    assert await changes.open_request(PublicRef(ref)) is not None
    done = await changes.confirm(PublicRef(ref))

    assert done.cancelled is not None
    assert done.moved is None
    assert uow.state.bookings[booking_id].status is BookingStatus.CANCELLED
    status = await changes.status(CALLER, request.public_id)
    assert status.status(NOW) is ChangeStatus.CONFIRMED
    with pytest.raises(DomainError) as again:
        await changes.confirm(PublicRef(ref))
    assert again.value.code is ErrorCode.CHANGE_REQUEST_NOT_FOUND


async def test_the_participant_confirms_a_new_time(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, token, code = await _booked(bookings)
    await changes.request(CALLER, await _demo(uow), PHONE, code, SLOT_0930)
    ref = PublicRef((await bookings.find(token)).booking.public_ref)
    done = await changes.confirm(ref)

    assert done.moved is not None
    assert done.moved.booking.start == SLOT_0930
    assert uow.state.bookings[booking_id].status is BookingStatus.CANCELLED


async def test_an_expired_or_moved_request_cannot_be_confirmed(
    changes: ChangeRequestService,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
) -> None:
    _id, token, code = await _booked(bookings)
    await changes.request(CALLER, await _demo(uow), PHONE, code, None)
    ref = PublicRef((await bookings.find(token)).booking.public_ref)
    clock.advance(timedelta(minutes=60))

    assert await changes.open_request(ref) is None
    with pytest.raises(DomainError) as late:
        await changes.confirm(ref)
    assert late.value.code is ErrorCode.CHANGE_REQUEST_NOT_FOUND


async def test_the_message_job_goes_stale_once_the_request_is_closed(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, _token, code = await _booked(bookings)
    request = await changes.request(CALLER, await _demo(uow), PHONE, code, None)
    booking = uow.state.bookings[booking_id]
    job = JobType.SEND_CHANGE_REQUEST

    assert not is_stale(SLOT_0900, booking, job, change=request, now=NOW)
    assert is_stale(SLOT_0900, booking, job, change=request.supersede(NOW), now=NOW)
    assert is_stale(SLOT_0900, booking, job, change=None, now=NOW)
    assert is_stale(SLOT_0930, booking, job, change=request, now=NOW)


async def test_the_shown_code_is_the_matching_one(
    changes: ChangeRequestService, bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    booking_id, _token, code = await _booked(bookings)

    booking = uow.state.bookings[booking_id]

    assert changes.code(booking, booking.group_id) == format_code(code)


async def test_switching_off_hides_the_code_and_stops_open_requests(
    changes: ChangeRequestService,
    bookings: BookingService,
    uow: InMemoryUnitOfWork,
    policy: dict[str, ChangePolicy],
) -> None:
    booking_id, token, code = await _booked(bookings)
    await changes.request(CALLER, await _demo(uow), PHONE, code, None)
    ref = PublicRef((await bookings.find(token)).booking.public_ref)
    policy["value"] = ChangePolicy(enabled=False)

    assert changes.code(uow.state.bookings[booking_id], 1) is None
    assert await changes.open_request(ref) is None
    with pytest.raises(DomainError) as off:
        await changes.confirm(ref)
    assert off.value.code is ErrorCode.CHANGE_REQUEST_NOT_FOUND
    assert uow.state.bookings[booking_id].status is BookingStatus.CONFIRMED
