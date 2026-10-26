"""OrganisationService: only the superuser creates and edits organisations, and it is audited."""

from dataclasses import replace

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.branding import Branding
from app.domain.errors import DomainError, ErrorCode
from app.domain.organisations import NewOrganisation, OrganisationService, OrganisationSettings
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW

SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
LOCALES = ("nl", "en")


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State(next_id=2)
    state.organisation_slugs = {1: "demo"}
    state.organisations = {1: Organisation(1, "Demo-gemeente", "demo")}
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> OrganisationService:
    clock = FixedClock(NOW)
    return OrganisationService(uow, clock, AdminService(uow, clock), LOCALES)


async def test_superuser_creates_an_organisation_and_it_is_audited(
    service: OrganisationService, uow: InMemoryUnitOfWork
) -> None:
    created = await service.create(SUPER, NewOrganisation("Gemeente Noord", "noord", "en"))

    assert (created.name, created.slug, created.default_locale) == ("Gemeente Noord", "noord", "en")
    assert await service.get(SUPER, "noord") == created
    assert [o.slug for o in await service.organisations(SUPER)] == ["demo", "noord"]
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation, event.detail) == (
        "organisation.manage",
        Outcome.SUCCESS,
        "noord",
        "create",
    )


@pytest.mark.parametrize(
    ("name", "slug", "locale", "field"),
    [
        ("", "noord", "nl", "name"),
        ("x" * 201, "noord", "nl", "name"),
        ("Noord\n", "noord", "nl", "name"),
        ("Noord", "Noord", "nl", "slug"),
        ("Noord", "noord-", "nl", "slug"),
        ("Noord", "a" * 65, "nl", "slug"),
        ("Noord", "noord", "de", "default_locale"),
    ],
)
async def test_invalid_input_is_refused_with_its_field(
    service: OrganisationService, name: str, slug: str, locale: str, field: str
) -> None:
    with pytest.raises(DomainError) as error:
        await service.create(SUPER, NewOrganisation(name, slug, locale))
    assert (error.value.code, error.value.field) == (ErrorCode.INVALID_CONFIGURATION, field)


@pytest.mark.parametrize("slug", ["demo", "new"])
async def test_a_taken_or_reserved_slug_is_refused(
    service: OrganisationService, uow: InMemoryUnitOfWork, slug: str
) -> None:
    with pytest.raises(DomainError) as error:
        await service.create(SUPER, NewOrganisation("Dubbel", slug, "nl"))
    assert (error.value.code, error.value.field) == (ErrorCode.SLUG_TAKEN, "slug")
    assert len(uow.state.organisations) == 1


async def test_org_admin_may_not_create_or_edit_organisations(
    service: OrganisationService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as created:
        await service.create(ORG_ADMIN, NewOrganisation("Noord", "noord", "nl"))
    with pytest.raises(DomainError) as updated:
        await service.update(ORG_ADMIN, "demo", OrganisationSettings("Eigen naam", "nl"))
    with pytest.raises(DomainError) as listed:
        await service.organisations(ORG_ADMIN)

    for error in (created, updated, listed):
        assert error.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.organisations[1].name == "Demo-gemeente"
    assert [e.outcome for e in uow.state.audit] == [Outcome.DENIED] * 3


async def test_update_changes_name_and_locale_and_names_the_fields(
    service: OrganisationService, uow: InMemoryUnitOfWork
) -> None:
    changed = await service.update(SUPER, "demo", OrganisationSettings("Gemeente Demo", "en"))

    assert changed == ["default_locale", "name"]
    stored = uow.state.organisations[1]
    assert (stored.name, stored.slug, stored.default_locale) == ("Gemeente Demo", "demo", "en")
    assert uow.state.audit[-1].detail == "default_locale,name"  # field names, never values


async def test_update_keeps_the_branding(
    service: OrganisationService, uow: InMemoryUnitOfWork
) -> None:
    branding = Branding("Gemeente Demo", "#154273")
    uow.state.organisations[1] = replace(uow.state.organisations[1], branding=branding)

    await service.update(SUPER, "demo", OrganisationSettings("Nieuw", "nl"))

    assert uow.state.organisations[1].branding == branding


async def test_saving_the_same_settings_changes_nothing(
    service: OrganisationService, uow: InMemoryUnitOfWork
) -> None:
    assert await service.update(SUPER, "demo", OrganisationSettings("Demo-gemeente", "nl")) == []
    assert uow.state.audit == []


async def test_invalid_update_is_refused(service: OrganisationService) -> None:
    with pytest.raises(DomainError) as error:
        await service.update(SUPER, "demo", OrganisationSettings("Demo", "fr"))
    assert (error.value.code, error.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "default_locale",
    )


async def test_unknown_organisation(service: OrganisationService) -> None:
    with pytest.raises(DomainError) as read:
        await service.get(SUPER, "nope")
    with pytest.raises(DomainError) as updated:
        await service.update(SUPER, "nope", OrganisationSettings("X", "nl"))
    assert read.value.code is updated.value.code is ErrorCode.ORGANISATION_NOT_FOUND


def test_service_needs_at_least_one_locale(uow: InMemoryUnitOfWork) -> None:
    clock = FixedClock(NOW)
    with pytest.raises(ValueError, match="locale"):
        OrganisationService(uow, clock, AdminService(uow, clock), ())
