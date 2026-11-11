"""CapacityService (ADR 0121): authorisation, audit details, limits, archived and legacy pools."""

import pytest

from app.domain import audit
from app.domain.admin import AdminService
from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.capacity_service import CapacityService
from app.domain.errors import DomainError, ErrorCode
from app.domain.skill_service import SkillService
from tests.fakes import clock as fake_clock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


def make(uow: InMemoryUnitOfWork, limit: int = 2) -> CapacityService:
    clock = fake_clock.FixedClock(NOW)
    return CapacityService(uow, clock, AdminService(uow, clock), lambda: limit)


@pytest.fixture
def service(uow: InMemoryUnitOfWork) -> CapacityService:
    return make(uow)


async def skill(uow: InMemoryUnitOfWork, code: str = "passport", org: str = "demo") -> int:
    clock = fake_clock.FixedClock(NOW)
    made = await SkillService(uow, clock, AdminService(uow, clock), 10).create(
        SUPER, org, code, code.title(), code.title()
    )
    assert made.id is not None
    return made.id


def details(uow: InMemoryUnitOfWork) -> list[str]:
    return [e.detail for e in uow.state.audit if e.outcome is audit.Outcome.SUCCESS]


async def legacy(uow: InMemoryUnitOfWork) -> int:
    async with uow.locked() as tx:
        stored = await tx.save_capacity_pool(
            Scope(everything=True),
            CapacityPool(None, 1, "Demo", PoolMode.LEGACY_GROUP, group_id=1),
        )
    assert stored.id is not None
    return stored.id


async def test_a_refusal_is_audited_and_changes_nothing(
    service: CapacityService, uow: InMemoryUnitOfWork
) -> None:
    for who in (GROUP_ADMIN, OUTSIDER, AUDITOR):
        with pytest.raises(DomainError) as refused:
            await service.create(who, "demo", "loket", "Loket", set())
        assert refused.value.code is ErrorCode.NOT_PERMITTED
    with pytest.raises(DomainError):
        await service.overview(GROUP_ADMIN, "demo")

    assert len([e for e in uow.state.audit if e.outcome is audit.Outcome.DENIED]) == 4
    assert not uow.state.pools


async def test_the_overview_is_read_under_organisation_view_and_says_who_may_change(
    service: CapacityService,
) -> None:
    await service.create(ORG_ADMIN, "demo", "loket", "Loket", set())

    seen = await service.overview(AUDITOR, "demo")
    own = await service.overview(ORG_ADMIN, "demo")

    assert [line.pool.name for line in seen.lines] == ["Loket"]
    assert (seen.editable, own.editable, own.limit, own.can_add) == (False, True, 2, True)
    with pytest.raises(DomainError):  # the form and the pages of one pool stay a manage right
        await service.form(AUDITOR, "demo")


async def test_changes_are_audited_by_pool_number_and_an_equal_save_writes_nothing(
    service: CapacityService, uow: InMemoryUnitOfWork
) -> None:
    passport = await skill(uow)
    pool = await service.create(ORG_ADMIN, "demo", "loket", " Loket ", {passport})
    assert pool.id is not None
    assert await service.update(ORG_ADMIN, "demo", pool.id, "Loket", {passport}) is False
    assert await service.update(ORG_ADMIN, "demo", pool.id, "Spreekuur", {passport}) is True
    await service.archive(ORG_ADMIN, "demo", pool.id)
    await service.archive(ORG_ADMIN, "demo", pool.id)

    assert details(uow)[1:] == [
        "capacity_pool.created,pool:loket",
        "capacity_pool.changed,pool:loket",
        "capacity_pool.archived,pool:loket",
    ]
    assert all("Spreekuur" not in d for d in details(uow))


async def test_an_archived_pool_has_no_form_and_cannot_be_changed(
    service: CapacityService,
) -> None:
    pool = await service.create(ORG_ADMIN, "demo", "loket", "Loket", set())
    assert pool.id is not None
    await service.archive(ORG_ADMIN, "demo", pool.id)

    for call in (
        service.edit(ORG_ADMIN, "demo", pool.id),
        service.page(ORG_ADMIN, "demo", pool.id),
        service.update(ORG_ADMIN, "demo", pool.id, "x", set()),
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert refused.value.code is ErrorCode.CONFIGURATION_OBJECT_MISSING


async def test_a_legacy_pool_is_listed_and_never_opened_changed_or_archived(
    service: CapacityService, uow: InMemoryUnitOfWork
) -> None:
    pool_id = await legacy(uow)

    listed = await service.overview(ORG_ADMIN, "demo")
    for call in (
        service.edit(ORG_ADMIN, "demo", pool_id),
        service.update(ORG_ADMIN, "demo", pool_id, "x", set()),
        service.archive(ORG_ADMIN, "demo", pool_id),
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert (refused.value.code, refused.value.field) == (
            ErrorCode.INVALID_CONFIGURATION,
            "mode",
        )

    assert [line.pool.mode for line in listed.lines] == [PoolMode.LEGACY_GROUP]
    assert details(uow) == []


async def test_the_limit_counts_counted_pools_not_archived_and_not_legacy_ones(
    service: CapacityService, uow: InMemoryUnitOfWork
) -> None:
    await legacy(uow)
    first = await service.create(ORG_ADMIN, "demo", "een", "Een", set())
    await service.create(ORG_ADMIN, "demo", "twee", "Twee", set())

    with pytest.raises(DomainError) as refused:
        await service.create(ORG_ADMIN, "demo", "drie", "Drie", set())
    full = await service.overview(ORG_ADMIN, "demo")
    assert first.id is not None
    await service.archive(ORG_ADMIN, "demo", first.id)
    await service.create(ORG_ADMIN, "demo", "drie", "Drie", set())

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "pool_limit",
    )
    assert full.can_add is False


async def test_a_pool_takes_only_own_skills_and_another_organisations_pool_is_missing(
    service: CapacityService, uow: InMemoryUnitOfWork
) -> None:
    theirs_skill = await skill(uow, "theirs", "andere")
    theirs = await service.create(SUPER, "andere", "hun", "Hun", set())
    assert theirs.id is not None

    with pytest.raises(DomainError) as foreign_skill:
        await service.create(ORG_ADMIN, "demo", "loket", "Loket", {theirs_skill})
    for call in (
        service.edit(ORG_ADMIN, "demo", theirs.id),
        service.update(ORG_ADMIN, "demo", theirs.id, "x", set()),
        service.archive(ORG_ADMIN, "demo", theirs.id),
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert refused.value.code is ErrorCode.CONFIGURATION_OBJECT_MISSING

    assert foreign_skill.value.field == "guaranteed_skill_ids"


async def test_a_pool_code_is_required_unique_fixed_and_audited_instead_of_the_number(
    service: CapacityService, uow: InMemoryUnitOfWork
) -> None:
    first = await service.create(ORG_ADMIN, "demo", " eerste ", "Eerste", set())
    assert first.code == "eerste"
    assert first.id is not None

    for code, name, field in (
        ("", "Zonder", "pool_code"),
        ("Hoofdletter", "Hoofd", "pool_code"),
        ("eerste", "Tweede", "pool_code"),
        ("tweede", " EERSTE ", "pool_name"),
    ):
        with pytest.raises(DomainError) as refused:
            await service.create(ORG_ADMIN, "demo", code, name, set())
        assert refused.value.field == field
    assert (await service.page(ORG_ADMIN, "demo", first.id)).line.pool.code == "eerste"
    await service.update(ORG_ADMIN, "demo", first.id, "Hernoemd", set())  # the code stays
    assert (await service.page(ORG_ADMIN, "demo", first.id)).line.pool.code == "eerste"
    assert details(uow)[-1] == "capacity_pool.changed,pool:eerste"
    assert all(f"pool:{first.id}" != d.split(",")[-1] for d in details(uow))
