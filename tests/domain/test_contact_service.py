"""ContactService: who may change an organisation's contact block, and what is audited."""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal
from app.domain.contact_service import ContactService
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW

PHONE = ContactOption.create(ContactKind.PHONE, "14 020", hours={"nl": "ma-vr 9-17"})
CHAT = ContactOption.create(ContactKind.CHAT, "https://chat.gemeente.example")
BLOCK = ContactBlock((PHONE, CHAT, PHONE), {"nl": "Andere manieren"})


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> ContactService:
    clock = FixedClock(NOW)
    return ContactService(uow, clock, AdminService(uow, clock))


async def test_an_organisation_starts_without_a_block(service: ContactService) -> None:
    assert await service.public(1) is None
    view = await service.view(ORG_ADMIN, "demo")
    assert (view.organisation.slug, view.block) == ("demo", None)


async def test_organisation_admin_saves_the_block_and_it_is_audited_with_counts(
    service: ContactService, uow: InMemoryUnitOfWork
) -> None:
    assert await service.save(ORG_ADMIN, "demo", BLOCK)

    assert await service.public(1) == BLOCK
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation, event.group) == (
        "organisation.closure",
        Outcome.SUCCESS,
        "demo",
        None,
    )
    assert event.detail == "contacts.update,chat:1,phone:2"  # counts, never numbers or texts


async def test_saving_the_same_block_changes_nothing(
    service: ContactService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(SUPER, "demo", BLOCK)
    events = len(uow.state.audit)

    assert not await service.save(SUPER, "demo", BLOCK)
    assert len(uow.state.audit) == events


async def test_an_empty_block_removes_it(service: ContactService, uow: InMemoryUnitOfWork) -> None:
    await service.save(SUPER, "demo", BLOCK)

    assert await service.save(SUPER, "demo", ContactBlock((), {"nl": "Alleen een kop"}))
    assert await service.public(1) is None
    assert uow.state.audit[-1].detail == "contacts.remove"


async def test_removing_a_block_that_is_not_there_changes_nothing(
    service: ContactService,
) -> None:
    assert not await service.save(SUPER, "demo", ContactBlock(()))


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OUTSIDER])
async def test_group_admins_and_other_organisations_are_refused_and_audited(
    service: ContactService, uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.save(principal, "demo", BLOCK)
    assert refused.value.code is ErrorCode.NOT_PERMITTED
    with pytest.raises(DomainError):
        await service.view(principal, "demo")

    assert await service.public(1) is None
    assert uow.state.audit[-1].outcome is Outcome.DENIED


async def test_an_unknown_organisation_is_not_found(service: ContactService) -> None:
    with pytest.raises(DomainError) as missing:
        await service.save(SUPER, "onbekend", BLOCK)
    assert missing.value.code is ErrorCode.ORGANISATION_NOT_FOUND
