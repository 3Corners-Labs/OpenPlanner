"""Subjects bound to staffing blocks, and the subject on a booking (ADR 0081 points 2 and 4)."""

from dataclasses import replace
from datetime import time

import pytest

from app.domain.admin import AdminService
from app.domain.availability import slots_on
from app.domain.errors import DomainError, ErrorCode
from app.domain.planning import PlanningService
from app.domain.routing import Attribute, GroupRouting, Subject
from app.domain.scheduling import ScheduleRule, TimeWindow
from app.domain.services import BookingService, ContactInput
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.closures import three_groups
from tests.support.domain import ADA, DEMO_ORG_ADMIN, MONDAY, NOW, SLOT_0900, make_group

MORNING = TimeWindow(time(9), time(10), 2, subjects=("move",))
LATER = TimeWindow(time(10), time(11), 1)
ROUTING = GroupRouting(
    (
        Subject.create("general", {"nl": "Algemeen", "en": "General"}),
        Subject.create("move", {"nl": "Verhuizing", "en": "Moving"}),
    ),
    (Attribute.PREFERRED_LANGUAGE,),
)
SLOT_1000 = SLOT_0900.replace(hour=9)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = three_groups()
    state.groups[1] = replace(state.groups[1], routing=ROUTING)
    state.rules[1] = [ScheduleRule(0, MORNING), ScheduleRule(0, LATER)]
    return InMemoryUnitOfWork(state)


def answers(subject: str, language: str = "nl") -> ContactInput:
    return replace(ADA, answers={"_subject": subject, "_preferred_language": language})


def test_a_slot_carries_the_subjects_all_its_blocks_offer() -> None:
    group = make_group(slot_minutes=60)
    straddling = [
        TimeWindow(time(9), time(9, 30), 1, subjects=("a", "b")),
        TimeWindow(time(9, 30), time(10, 30), 1, subjects=("b",)),
    ]
    slot = slots_on(group, MONDAY, straddling)[0]
    assert slot.subjects == frozenset({"b"})
    assert slot.offers_subject("b")
    assert not slot.offers_subject("a")
    assert slot.offers_subject(None)
    assert slots_on(group, MONDAY, [LATER])[0].subjects is None


def test_a_block_validates_its_subject_codes() -> None:
    with pytest.raises(DomainError):
        TimeWindow(time(9), time(10), 1, subjects=("b", "a"))  # not sorted
    with pytest.raises(DomainError):
        TimeWindow(time(9), time(10), 1, subjects=("Bad",))
    assert not TimeWindow(time(9), time(10), 1, subjects=("a",)).offers_subject("b")


async def test_availability_per_subject(bookings: BookingService) -> None:
    every = {s.start for s in await bookings.availability("demo", MONDAY, MONDAY)}
    moves = {s.start for s in await bookings.availability("demo", MONDAY, MONDAY, "move")}
    general = {s.start for s in await bookings.availability("demo", MONDAY, MONDAY, "general")}
    assert {SLOT_0900, SLOT_1000} <= moves
    assert SLOT_0900 not in general
    assert SLOT_1000 in general
    assert every == moves


async def test_an_unknown_subject_in_the_query_shows_the_plain_agenda(
    bookings: BookingService,
) -> None:
    every = await bookings.availability("demo", MONDAY, MONDAY)
    assert await bookings.availability("demo", MONDAY, MONDAY, "nope") == every


async def test_the_booking_keeps_the_codes_the_participant_chose(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await bookings.book(
        group_slug="demo", start=SLOT_0900, contact=answers("move", "en"), locale="nl"
    )
    routing = receipt.booking.routing
    assert routing is not None
    assert (routing.subject, routing.attribute(Attribute.PREFERRED_LANGUAGE)) == ("move", "en")
    assert uow.state.bookings[receipt.booking.id].routing == routing  # type: ignore[index]


async def test_a_slot_whose_block_does_not_offer_the_subject_is_unavailable(
    bookings: BookingService,
) -> None:
    with pytest.raises(DomainError) as refused:
        await bookings.book(
            group_slug="demo", start=SLOT_0900, contact=answers("general"), locale="nl"
        )
    assert refused.value.code is ErrorCode.SLOT_UNAVAILABLE


async def test_a_missing_or_crafted_subject_is_refused_by_its_key(
    bookings: BookingService,
) -> None:
    for contact in (ADA, answers("Verhuizing")):
        with pytest.raises(DomainError) as refused:
            await bookings.book(group_slug="demo", start=SLOT_1000, contact=contact, locale="nl")
        assert (refused.value.code, refused.value.field) == (ErrorCode.INVALID_ANSWER, "_subject")


async def test_a_routing_answer_is_not_taken_for_a_custom_field(
    bookings: BookingService,
) -> None:
    receipt = await bookings.book(
        group_slug="demo", start=SLOT_1000, contact=answers("general"), locale="nl"
    )
    assert receipt.booking.contact is not None
    assert receipt.booking.contact.answers is None


async def test_moving_keeps_the_subject_and_its_blocks(
    bookings: BookingService, uow: InMemoryUnitOfWork
) -> None:
    receipt = await bookings.book(
        group_slug="demo", start=SLOT_1000, contact=answers("general"), locale="nl"
    )
    await bookings.confirm(receipt.management_token)
    with pytest.raises(DomainError) as refused:
        await bookings.reschedule(receipt.management_token, SLOT_0900)
    assert refused.value.code is ErrorCode.SLOT_UNAVAILABLE


async def test_a_group_without_routing_books_as_before(bookings: BookingService) -> None:
    receipt = await bookings.book(group_slug="demo-twee", start=SLOT_0900, contact=ADA, locale="nl")
    assert receipt.booking.routing is None


async def test_a_block_may_only_offer_the_groups_subjects(uow: InMemoryUnitOfWork) -> None:
    clock = FixedClock(NOW)
    planning = PlanningService(uow, clock, AdminService(uow, clock))
    with pytest.raises(DomainError) as refused:
        await planning.add_rule(
            DEMO_ORG_ADMIN,
            "demo",
            ScheduleRule(1, TimeWindow(time(9), time(10), 1, subjects=("x",))),
        )
    assert refused.value.field == "subjects"
    await planning.add_rule(
        DEMO_ORG_ADMIN,
        "demo",
        ScheduleRule(1, TimeWindow(time(9), time(10), 1, subjects=("move",))),
    )
