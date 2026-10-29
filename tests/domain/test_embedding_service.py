"""EmbeddingService: who may change the allowed origins, what is audited, what is allowed."""

from dataclasses import replace

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.authorization import Principal
from app.domain.embedding import AllowedOrigins
from app.domain.embedding_service import EmbeddingService
from app.domain.errors import DomainError, ErrorCode
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW

SITE = "https://www.gemeente.nl"
WORK = "https://werk.gemeente.nl"


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def _service(uow: InMemoryUnitOfWork, *, allow_local: bool = False) -> EmbeddingService:
    clock = fake_clock.FixedClock(NOW)
    return EmbeddingService(
        uow, clock, AdminService(uow, clock), max_origins=3, allow_local=allow_local
    )


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> EmbeddingService:
    return _service(uow)


async def test_an_organisation_starts_without_origins_and_with_no_group_embedded(
    service: EmbeddingService,
) -> None:
    view = await service.view(ORG_ADMIN, "demo")

    assert (view.organisation.slug, view.allowed) == ("demo", AllowedOrigins())
    assert [g.group.slug for g in view.groups] == ["demo"]
    assert view.embedded_groups == ()


async def test_organisation_admin_saves_origins_and_it_is_audited_with_counts(
    service: EmbeddingService, uow: InMemoryUnitOfWork
) -> None:
    assert await service.save(ORG_ADMIN, "demo", f"{SITE}/\n{WORK}")

    assert uow.state.allowed_origins[1] == AllowedOrigins((SITE, WORK))
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.organisation, event.group) == (
        "embedding.manage",
        audit.Outcome.SUCCESS,
        "demo",
        None,
    )
    assert event.detail == "origins.update,added:2,removed:0,total:2"  # never the origins

    assert await service.save(ORG_ADMIN, "demo", WORK)
    assert uow.state.audit[-1].detail == "origins.update,added:0,removed:1,total:1"
    assert await service.save(ORG_ADMIN, "demo", "")
    assert uow.state.audit[-1].detail == "origins.update,added:0,removed:1,total:0"


async def test_saving_the_same_origins_changes_nothing(
    service: EmbeddingService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(SUPER, "demo", SITE)
    events = len(uow.state.audit)

    assert not await service.save(SUPER, "demo", f"  {SITE}/  \n\n")
    assert len(uow.state.audit) == events


async def test_a_bad_line_or_too_many_origins_store_nothing(
    service: EmbeddingService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as bad:
        await service.save(ORG_ADMIN, "demo", f"{SITE}\nhttp://www.gemeente.nl")
    assert bad.value.field == "origin_2"
    with pytest.raises(DomainError) as many:
        await service.save(ORG_ADMIN, "demo", "\n".join(f"https://s{n}.nl" for n in range(4)))
    assert many.value.field == "origins_count"

    assert 1 not in uow.state.allowed_origins


async def test_localhost_only_where_the_installation_allows_it(uow: InMemoryUnitOfWork) -> None:
    with pytest.raises(DomainError):
        await _service(uow).save(SUPER, "demo", "http://localhost:5173")

    assert await _service(uow, allow_local=True).save(SUPER, "demo", "http://localhost:5173")


@pytest.mark.parametrize("principal", [GROUP_ADMIN, OUTSIDER])
async def test_group_admins_and_other_organisations_are_refused_and_audited(
    service: EmbeddingService, uow: InMemoryUnitOfWork, principal: Principal
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.save(principal, "demo", SITE)
    assert refused.value.code is ErrorCode.NOT_PERMITTED
    with pytest.raises(DomainError):
        await service.view(principal, "demo")

    assert 1 not in uow.state.allowed_origins
    assert uow.state.audit[-1].outcome is audit.Outcome.DENIED


async def test_an_unknown_organisation_is_not_found(service: EmbeddingService) -> None:
    with pytest.raises(DomainError) as missing:
        await service.save(SUPER, "onbekend", SITE)
    assert missing.value.code is ErrorCode.ORGANISATION_NOT_FOUND


async def test_the_overview_lists_only_organisations_in_scope(
    service: EmbeddingService, uow: InMemoryUnitOfWork
) -> None:
    uow.state.groups[1] = replace(uow.state.groups[1], embedding_enabled=True)
    await service.save(SUPER, "demo", SITE)

    own = await service.organisations(ORG_ADMIN)
    every = await service.organisations(SUPER)

    assert [(o.organisation.slug, o.allowed.origins) for o in own] == [("demo", (SITE,))]
    assert [g.group.slug for g in own[0].embedded_groups] == ["demo"]
    assert {o.organisation.slug for o in every} == {"demo", "andere"}
    assert await service.organisations(GROUP_ADMIN) == []


async def test_a_group_is_allowed_only_when_switched_on_and_the_origin_is_listed(
    service: EmbeddingService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(SUPER, "demo", SITE)
    await service.save(SUPER, "andere", WORK)
    demo = uow.state.groups[1]
    on = replace(demo, embedding_enabled=True)

    assert not await service.allows(demo, SITE)  # not switched on
    assert await service.allows(on, SITE)
    assert not await service.allows(on, WORK)  # another organisation's site
    assert not await service.allows(on, "https://www.gemeente.nl.evil.example")
    assert not await service.allows(on, None)
