"""BrandingService: who may change an organisation's branding, and what is audited."""

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.branding import Branding, Logo
from app.domain.branding_service import BrandingService
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, make_group

SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisation_slugs = {1: "demo"}
    state.organisations = {1: Organisation(1, "Demo-gemeente", "demo")}
    state.groups[1] = make_group(id=1, organisation_id=1, slug="demo", name="Demo")
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> BrandingService:
    clock = FixedClock(NOW)
    return BrandingService(uow, clock, AdminService(uow, clock))


async def test_without_branding_the_defaults_apply(service: BrandingService) -> None:
    group = make_group(id=1, organisation_id=1, slug="demo", name="Demo")
    organisation = await service.for_group(group)
    assert organisation.branding == Branding()
    assert organisation.name == "Demo-gemeente"


async def test_superuser_changes_name_and_colour_and_it_is_audited(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    changed = await service.update(SUPER, "demo", Branding("Gemeente Demo", "#154273"))

    assert changed == ["accent_color", "display_name"]
    assert uow.state.organisations[1].branding == Branding("Gemeente Demo", "#154273")
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation) == (
        "branding.manage",
        Outcome.SUCCESS,
        "demo",
    )
    assert event.detail == "accent_color,display_name"  # field names, never values


async def test_saving_the_same_branding_changes_nothing(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    assert await service.update(SUPER, "demo", Branding()) == []
    assert uow.state.audit == []


async def test_org_admin_may_not_change_branding_handover_section_6(
    service: BrandingService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as error:
        await service.update(ORG_ADMIN, "demo", Branding("X"))
    assert error.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.audit[-1].outcome is Outcome.DENIED


async def test_unknown_organisation(service: BrandingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.update(SUPER, "nope", Branding())
    assert error.value.code is ErrorCode.ORGANISATION_NOT_FOUND


async def test_logo_upload_and_removal(service: BrandingService, uow: InMemoryUnitOfWork) -> None:
    logo = Logo.from_upload(PNG)
    await service.set_logo(SUPER, "demo", logo)

    assert uow.state.organisations[1].branding.logo == logo.ref
    assert await service.logo("demo") == logo
    assert uow.state.audit[-1].detail == "logo"

    await service.remove_logo(SUPER, "demo")
    assert uow.state.organisations[1].branding.logo is None
    assert await service.logo("demo") is None
    assert uow.state.audit[-1].detail == "logo.remove"


async def test_a_new_name_keeps_the_logo(service: BrandingService, uow: InMemoryUnitOfWork) -> None:
    await service.set_logo(SUPER, "demo", Logo.from_upload(PNG))
    await service.update(SUPER, "demo", Branding("Nieuw"))
    assert uow.state.organisations[1].branding.logo is not None


async def test_org_admin_may_not_upload_a_logo(service: BrandingService) -> None:
    with pytest.raises(DomainError) as error:
        await service.set_logo(ORG_ADMIN, "demo", Logo.from_upload(PNG))
    assert error.value.code is ErrorCode.NOT_PERMITTED


async def test_public_lookup_by_slug(service: BrandingService) -> None:
    assert (await service.public("demo")).slug == "demo"
    with pytest.raises(DomainError) as error:
        await service.public("nope")
    assert error.value.code is ErrorCode.ORGANISATION_NOT_FOUND
