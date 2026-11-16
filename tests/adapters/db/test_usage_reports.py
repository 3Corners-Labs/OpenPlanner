"""The usage reports against SQLite: read inside the scope, small cells hidden (ADR 0117 R1, R2)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import AuditEventRow, GroupRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Principal, Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.usage import FactKind, UsageFact, UsageOutcome, hour_of
from app.domain.usage_report_service import UsageReportService
from app.domain.usage_rollup_service import UsageRollup
from tests.fakes.clock import FixedClock
from tests.support.principals import principal
from tests.support.seed_rows import seed_group

NOW = datetime(2026, 11, 20, 12, 30, tzinfo=UTC)
DAY = date(2026, 11, 18)
HOUR = datetime(2026, 11, 18, 10, tzinfo=UTC)

SUPER = principal("s", "S", {Role.SUPERUSER})
ORG_ALFA = principal("o", "O", {Role.ORG_ADMIN}, organisations={"org-alfa"})
GROUP_ALFA = principal("g", "G", {Role.GROUP_ADMIN}, groups=[("org-alfa", "alfa")])
AUDITOR_ALFA = principal("a", "A", {Role.AUDITOR}, organisations={"org-alfa"})
AGENT_ALFA = principal("w", "W", {Role.AGENT}, groups=[("org-alfa", "alfa")])
ORG_BETA = principal("b", "B", {Role.ORG_ADMIN}, organisations={"org-beta"})


async def _ids(engine: AsyncEngine) -> dict[str, tuple[int, int]]:
    async with AsyncSession(engine) as session:
        rows = await session.execute(select(GroupRow.slug, GroupRow.id, GroupRow.organisation_id))
        return {slug: (gid, oid) for slug, gid, oid in rows}


async def _world(engine: AsyncEngine, alfa: int = 20, beta: int = 20, org_min: int = 3):  # type: ignore[no-untyped-def]
    await seed_group(engine, slug="alfa")
    await seed_group(engine, slug="beta")
    ids = await _ids(engine)
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    async with uow.locked() as tx:
        for slug, count in (("alfa", alfa), ("beta", beta)):
            group, organisation = ids[slug]
            for _ in range(count):
                await tx.record_usage(
                    UsageFact(
                        FactKind.BOOKING_CREATED, HOUR, organisation, group, value_seconds=3600
                    )
                )
            for _ in range(count):
                await tx.record_usage(
                    UsageFact(
                        FactKind.BOOKING_CANCELLED,
                        HOUR,
                        organisation,
                        group,
                        outcome=UsageOutcome.CANCELLED_BY_PLANNER,
                    )
                )
    await UsageRollup(
        uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25
    ).run_if_due()
    service = UsageReportService(
        uow, AdminService(uow, clock), clock, lambda: 5, lambda organisation: org_min
    )
    return service, ids


async def test_a_group_admin_reads_the_own_groups_figures(engine: AsyncEngine) -> None:
    service, _ = await _world(engine)

    result = await service.volume(GROUP_ALFA, group_slug="alfa", first_day=DAY, last_day=DAY)

    assert result.report.created_total == 20
    assert {r.code: r.count for r in result.report.outcomes.rows}["cancelled_by_planner"] == 20
    assert result.report.as_of == NOW
    assert [g.slug for g in result.groups] == ["alfa"]


async def test_another_organisations_group_is_out_of_scope_and_audited(
    engine: AsyncEngine,
) -> None:
    service, _ = await _world(engine)

    with pytest.raises(DomainError) as refused:
        await service.volume(ORG_BETA, group_slug="alfa", first_day=DAY, last_day=DAY)

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    async with AsyncSession(engine) as session:
        denied = list(
            await session.scalars(select(AuditEventRow).where(AuditEventRow.outcome == "denied"))
        )
    assert any(e.action == "reports.view" for e in denied)


async def test_an_organisation_page_adds_only_that_organisations_groups(
    engine: AsyncEngine,
) -> None:
    service, _ = await _world(engine)

    alfa = await service.volume(ORG_ALFA, organisation_slug="org-alfa", first_day=DAY, last_day=DAY)

    assert alfa.report.created_total == 20
    assert [g.slug for g in alfa.groups] == ["alfa"]


async def test_an_organisation_admin_cannot_open_another_organisations_page(
    engine: AsyncEngine,
) -> None:
    service, _ = await _world(engine)

    with pytest.raises(DomainError) as refused:
        await service.volume(ORG_ALFA, organisation_slug="org-beta", first_day=DAY, last_day=DAY)

    assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_a_group_admin_has_no_organisation_page_only_the_own_group_pages(
    engine: AsyncEngine,
) -> None:
    service, _ = await _world(engine)

    with pytest.raises(DomainError) as refused:
        await service.volume(GROUP_ALFA, organisation_slug="org-alfa", first_day=DAY, last_day=DAY)

    assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_the_superuser_reads_every_organisation(engine: AsyncEngine) -> None:
    service, _ = await _world(engine)

    beta = await service.volume(SUPER, organisation_slug="org-beta", first_day=DAY, last_day=DAY)

    assert beta.report.created_total == 20


async def test_the_auditor_reads_counts_and_the_agent_nothing(engine: AsyncEngine) -> None:
    service, _ = await _world(engine)

    audited = await service.volume(
        AUDITOR_ALFA, organisation_slug="org-alfa", first_day=DAY, last_day=DAY
    )
    with pytest.raises(DomainError) as refused:
        await service.volume(AGENT_ALFA, group_slug="alfa", first_day=DAY, last_day=DAY)

    assert audited.report.created_total == 20
    assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_a_small_group_shows_less_than_the_threshold(engine: AsyncEngine) -> None:
    service, _ = await _world(engine, alfa=3)

    result = await service.volume(GROUP_ALFA, group_slug="alfa", first_day=DAY, last_day=DAY)

    assert result.report.created_total is None
    assert all(r.count is None for r in result.report.outcomes.rows)


async def test_an_organisation_can_raise_the_threshold_for_its_own_view(
    engine: AsyncEngine,
) -> None:
    service, _ = await _world(engine, alfa=20, org_min=30)

    result = await service.volume(GROUP_ALFA, group_slug="alfa", first_day=DAY, last_day=DAY)

    assert result.report.threshold == 30
    assert result.report.created_total is None


async def test_the_default_range_is_the_last_four_weeks(engine: AsyncEngine) -> None:
    service, _ = await _world(engine)

    result = await service.volume(GROUP_ALFA, group_slug="alfa")

    assert (result.report.last_day - result.report.first_day).days == 27
    assert result.report.last_day == date(2026, 11, 20)
    assert result.report.created_total == 20


@pytest.mark.parametrize(
    ("first", "last"),
    [(date(2026, 11, 20), date(2026, 11, 1)), (date(2024, 1, 1), date(2026, 11, 20))],
)
async def test_a_backwards_or_too_wide_range_is_refused(
    engine: AsyncEngine, first: date, last: date
) -> None:
    service, _ = await _world(engine)

    with pytest.raises(DomainError) as refused:
        await service.volume(GROUP_ALFA, group_slug="alfa", first_day=first, last_day=last)

    assert refused.value.code is ErrorCode.INVALID_RANGE


async def test_the_list_gives_each_group_a_count_and_no_total(engine: AsyncEngine) -> None:
    service, _ = await _world(engine, alfa=20, beta=2)

    rows = await service.headlines(SUPER)

    by_slug = {r.group.slug: r.created for r in rows}
    assert by_slug == {"alfa": 20, "beta": None}


async def test_the_list_of_a_group_admin_holds_only_the_own_groups(engine: AsyncEngine) -> None:
    service, _ = await _world(engine)

    rows = await service.headlines(GROUP_ALFA)

    assert [r.group.slug for r in rows] == ["alfa"]


async def test_the_agent_gets_no_list(engine: AsyncEngine) -> None:
    service, _ = await _world(engine)

    with pytest.raises(DomainError) as refused:
        await service.headlines(AGENT_ALFA)

    assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_the_hour_of_a_fact_decides_the_day_in_the_groups_time_zone(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, slug="alfa")
    ids = await _ids(engine)
    group, organisation = ids["alfa"]
    late = hour_of(datetime(2026, 11, 18, 23, tzinfo=UTC))  # 00:00 on the 19th in Amsterdam
    uow = SqlUnitOfWork(engine)
    clock = FixedClock(NOW)
    async with uow.locked() as tx:
        for _ in range(10):
            await tx.record_usage(UsageFact(FactKind.BOOKING_CREATED, late, organisation, group))
    await UsageRollup(
        uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25
    ).run_if_due()
    service = UsageReportService(uow, AdminService(uow, clock), clock, lambda: 5, lambda _: 3)

    result = await service.volume(
        GROUP_ALFA, group_slug="alfa", first_day=date(2026, 11, 18), last_day=date(2026, 11, 20)
    )

    days = {row.day: row.count for row in result.report.days}
    assert days[date(2026, 11, 19)] == 10
    assert days[date(2026, 11, 18)] is None


def test_principals_are_built_with_the_roles_the_tests_expect() -> None:
    assert isinstance(SUPER, Principal)
