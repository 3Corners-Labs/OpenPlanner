"""Closure content (ADR 0037 §2, CL-1): heading per locale and a copy of the contact block.

The closure copies the block that applies when it starts: the group's own block, or else the
organisation default. Editing either block afterwards leaves a running closure alone, unless
the update explicitly takes over the current contact options.
"""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.closure import MAX_HEADING_LENGTH, Closure
from app.domain.closure_service import ClosureInput, ClosureService
from app.domain.contact_service import ContactService
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.errors import DomainError, ErrorCode
from app.domain.services import BookingService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, two_organisations
from tests.support.closures import NOTICES
from tests.support.domain import NOW, TEST_TOKEN_KEY, make_group

ORGANISATION_BLOCK = ContactBlock((ContactOption.create(ContactKind.PHONE, "14 020"),))
GROUP_BLOCK = ContactBlock(
    (ContactOption.create(ContactKind.EMAIL, "loket@gemeente.example"),), {"nl": "Mail ons"}
)
HEADINGS = {"nl": "Storing", "en": "Outage"}


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = two_organisations(next_id=100)
    state.groups[3] = make_group(id=3, organisation_id=1, slug="demo-twee", name="Demo twee")
    return InMemoryUnitOfWork(state)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def contacts(uow: InMemoryUnitOfWork, clock: FixedClock) -> ContactService:
    return ContactService(uow, clock, AdminService(uow, clock))


@pytest.fixture
def closures(uow: InMemoryUnitOfWork, clock: FixedClock) -> ClosureService:
    admin = AdminService(uow, clock)
    return ClosureService(uow, clock, admin, BookingService(uow, clock, TEST_TOKEN_KEY))


async def _current(closures: ClosureService, slug: str = "demo") -> Closure:
    closure = (await closures.overview(ORG_ADMIN, slug)).closure
    assert closure is not None
    return closure


# --- the closure itself ---------------------------------------------------------------------


def test_a_closure_keeps_non_empty_headings_and_falls_back_to_dutch() -> None:
    closure = Closure(None, 1, NOW, None, NOTICES, NOW, {"nl": "  Storing ", "en": ""})

    assert closure.headings == {"nl": "Storing"}
    assert closure.heading("en") == "Storing"
    assert Closure(None, 1, NOW, None, NOTICES, NOW).heading("nl") is None


@pytest.mark.parametrize(
    ("headings", "field"),
    [
        ({"en": "x" * (MAX_HEADING_LENGTH + 1)}, "heading_en"),
        ({"nl": "twee\nregels"}, "heading_nl"),
        ({"de": "Störung"}, "heading_de"),
    ],
)
def test_a_heading_that_is_too_long_multi_line_or_in_another_locale_is_refused(
    headings: dict[str, str], field: str
) -> None:
    with pytest.raises(DomainError) as refused:
        Closure(None, 1, NOW, None, NOTICES, NOW, headings)

    assert (refused.value.code, refused.value.field) == (ErrorCode.INVALID_CONFIGURATION, field)


# --- the group's own block ------------------------------------------------------------------


async def test_group_admin_sets_the_groups_block_and_sees_the_organisation_default(
    contacts: ContactService, uow: InMemoryUnitOfWork
) -> None:
    await contacts.save(ORG_ADMIN, "demo", ORGANISATION_BLOCK)

    assert await contacts.save_group(GROUP_ADMIN, "demo", GROUP_BLOCK)

    view = await contacts.view_group(GROUP_ADMIN, "demo")
    assert (view.group.slug, view.block, view.organisation_default) == (
        "demo",
        GROUP_BLOCK,
        ORGANISATION_BLOCK,
    )
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation, event.group, event.detail) == (
        "closure.manage",
        Outcome.SUCCESS,
        "demo",
        "demo",
        "contacts.update,email:1",
    )


async def test_an_empty_group_block_removes_it_and_saving_it_twice_changes_nothing(
    contacts: ContactService, uow: InMemoryUnitOfWork
) -> None:
    await contacts.save_group(GROUP_ADMIN, "demo", GROUP_BLOCK)
    assert not await contacts.save_group(GROUP_ADMIN, "demo", GROUP_BLOCK)

    assert await contacts.save_group(GROUP_ADMIN, "demo", ContactBlock(()))

    assert (await contacts.view_group(GROUP_ADMIN, "demo")).block is None
    assert uow.state.audit[-1].detail == "contacts.remove"


async def test_another_groups_admin_cannot_read_or_change_the_groups_block(
    contacts: ContactService, uow: InMemoryUnitOfWork
) -> None:
    for attempt in (
        contacts.view_group(OUTSIDER, "demo"),
        contacts.save_group(OUTSIDER, "demo", GROUP_BLOCK),
    ):
        with pytest.raises(DomainError) as refused:
            await attempt
        assert refused.value.code is ErrorCode.NOT_PERMITTED

    assert uow.state.group_contact_blocks == {}
    assert uow.state.audit[-1].outcome is Outcome.DENIED


async def test_an_unknown_group_is_not_found(contacts: ContactService) -> None:
    with pytest.raises(DomainError) as refused:
        await contacts.view_group(ORG_ADMIN, "nope")

    assert refused.value.code in {ErrorCode.GROUP_NOT_FOUND, ErrorCode.NOT_PERMITTED}


# --- copying into the closure ---------------------------------------------------------------


async def test_a_closure_copies_the_organisation_default_when_the_group_has_none(
    closures: ClosureService, contacts: ContactService
) -> None:
    await contacts.save(ORG_ADMIN, "demo", ORGANISATION_BLOCK)

    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES, HEADINGS))

    closure = await _current(closures)
    assert (closure.contacts, closure.headings) == (ORGANISATION_BLOCK, HEADINGS)


async def test_the_groups_own_block_wins_and_later_edits_leave_the_closure_alone(
    closures: ClosureService, contacts: ContactService
) -> None:
    await contacts.save(ORG_ADMIN, "demo", ORGANISATION_BLOCK)
    await contacts.save_group(GROUP_ADMIN, "demo", GROUP_BLOCK)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))

    await contacts.save_group(GROUP_ADMIN, "demo", ContactBlock(()))
    await contacts.save(ORG_ADMIN, "demo", ContactBlock(()))

    assert (await _current(closures)).contacts == GROUP_BLOCK


async def test_a_closure_without_any_block_has_no_contacts(closures: ClosureService) -> None:
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES))

    closure = await _current(closures)
    assert (closure.contacts, closure.headings) == (None, {})


async def test_closing_the_organisation_copies_each_groups_own_block(
    closures: ClosureService, contacts: ContactService
) -> None:
    await contacts.save(ORG_ADMIN, "demo", ORGANISATION_BLOCK)
    await contacts.save_group(ORG_ADMIN, "demo-twee", GROUP_BLOCK)

    await closures.close_organisation(
        ORG_ADMIN, "demo", ClosureInput(None, None, NOTICES, HEADINGS)
    )

    first, second = await _current(closures, "demo"), await _current(closures, "demo-twee")
    assert (first.contacts, second.contacts) == (ORGANISATION_BLOCK, GROUP_BLOCK)
    assert first.headings == second.headings == HEADINGS


async def test_an_update_keeps_the_copy_unless_asked_to_take_over_the_current_block(
    closures: ClosureService, contacts: ContactService
) -> None:
    await contacts.save(ORG_ADMIN, "demo", ORGANISATION_BLOCK)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES, HEADINGS))
    await contacts.save_group(GROUP_ADMIN, "demo", GROUP_BLOCK)

    await closures.update(GROUP_ADMIN, "demo", None, NOTICES, notify=False)
    kept = await _current(closures)
    await closures.update(
        GROUP_ADMIN,
        "demo",
        None,
        NOTICES,
        notify=False,
        headings={"nl": "Nieuwe kop"},
        refresh_contacts=True,
    )
    refreshed = await _current(closures)

    assert (kept.contacts, kept.headings) == (ORGANISATION_BLOCK, HEADINGS)
    assert (refreshed.contacts, refreshed.headings) == (GROUP_BLOCK, {"nl": "Nieuwe kop"})


async def test_lifting_keeps_the_heading_and_the_copy_on_the_ended_closure(
    closures: ClosureService, contacts: ContactService, uow: InMemoryUnitOfWork
) -> None:
    await contacts.save(ORG_ADMIN, "demo", ORGANISATION_BLOCK)
    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES, HEADINGS))

    await closures.lift(GROUP_ADMIN, "demo", reinstate=False)

    (ended,) = uow.state.closures.values()
    assert (ended.contacts, ended.headings) == (ORGANISATION_BLOCK, HEADINGS)


async def test_the_audit_of_a_closure_names_counts_only(
    closures: ClosureService, contacts: ContactService, uow: InMemoryUnitOfWork
) -> None:
    await contacts.save(ORG_ADMIN, "demo", ORGANISATION_BLOCK)

    await closures.close_group(GROUP_ADMIN, "demo", ClosureInput(None, None, NOTICES, HEADINGS))

    assert uow.state.audit[-1].detail == "closure.start,bookings:0,contacts:1"
