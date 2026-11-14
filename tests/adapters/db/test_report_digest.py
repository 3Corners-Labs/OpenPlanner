"""The digest of the usage reports against SQLite: subscriptions and the runner (ADR 0117)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Role, Scope
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.report_digest import DigestPeriod, RunStatus
from app.domain.report_digest_model import DigestContent, DigestMail
from app.domain.report_digest_service import DigestAdmin, DigestRunner
from app.domain.staff import Membership, StaffMember
from app.domain.usage import FactKind, UsageFact, UsageOutcome, hour_of
from app.domain.usage_report_service import UsageReportService
from app.domain.usage_rollup_service import UsageRollup
from tests.fakes.clock import FixedClock
from tests.fakes.digest import FakeStaffAddresses
from tests.fakes.messaging import RecordingSender
from tests.support.principals import principal
from tests.support.seed_rows import seed_group

NOW = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)  # a Monday, 09:00 in Amsterdam
ORG = "org-alfa"
ORG_ADMIN = principal("sub-admin", "Olga", {Role.ORG_ADMIN}, organisations={ORG})
GROUP_ADMIN = principal("sub-group", "Gina", {Role.GROUP_ADMIN}, groups=[(ORG, "alfa")])
SECRET_ADDRESS = "gina@voorbeeld.nl"


def _compose(content: DigestContent, locale: str) -> DigestMail:
    return DigestMail(f"subject {locale}", f"text {content.link}", "<p>html</p>")


async def _world(engine: AsyncEngine) -> tuple[SqlUnitOfWork, int, int]:
    """One organisation with a group, an org admin and a group admin on record."""
    await seed_group(engine, slug="alfa")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        for subject, name, role, group in (
            ("sub-admin", "Olga", "org_admin", None),
            ("sub-group", "Gina", "group_admin", "alfa"),
            ("sub-agent", "Wim", "agent", "alfa"),
        ):
            await tx.save_staff(
                StaffMember(
                    subject,
                    name,
                    frozenset({Role(role)}),
                    frozenset({Membership(role, ORG, group)}),
                    NOW,
                    NOW,
                    NOW,
                )
            )
        olga = await tx.get_staff("sub-admin")
        gina = await tx.get_staff("sub-group")
        wim = await tx.get_staff("sub-agent")
    assert olga is not None
    assert gina is not None
    assert wim is not None
    assert olga.id is not None
    assert gina.id is not None
    assert wim.id is not None
    return uow, gina.id, wim.id


def _admin(
    uow: SqlUnitOfWork, limit: int = 20, addresses: dict[str, str] | None = None
) -> DigestAdmin:
    clock = FixedClock(NOW)
    return DigestAdmin(
        uow,
        AdminService(uow, clock),
        clock,
        lambda: limit,
        FakeStaffAddresses({"sub-admin": "o@voorbeeld.nl"} if addresses is None else addresses),
    )


def _runner(
    uow: SqlUnitOfWork,
    sender: RecordingSender | None,
    addresses: dict[str, str],
    *,
    now: datetime = NOW,
    numbers: bool = False,
    late_hours: int = 24,
) -> DigestRunner:
    clock = FixedClock(now)
    reports = UsageReportService(uow, AdminService(uow, clock), clock, lambda: 5, lambda _id: 3)
    return DigestRunner(
        uow,
        clock,
        reports,
        sender
        if sender is not None
        else RecordingSender(unconfigured=frozenset({ChannelKind.EMAIL})),
        FakeStaffAddresses(addresses),
        _compose,
        "https://planner.example",
        lambda _id: 7,
        lambda _id: numbers,
        lambda: late_hours,
        "Europe/Amsterdam",
    )


async def _subscribe(uow: SqlUnitOfWork, staff_id: int, group: str | None = None) -> int:
    return await _admin(uow).create(
        ORG_ADMIN,
        ORG,
        group_slug=group,
        period=DigestPeriod.DAILY,
        weekday=0,
        month_day=1,
        recipients=[staff_id],
    )


async def test_an_org_admin_subscribes_a_colleague_and_it_is_listed_and_audited(
    engine: AsyncEngine,
) -> None:
    uow, gina, _wim = await _world(engine)

    new_id = await _subscribe(uow, gina, "alfa")
    (row,) = await _admin(uow).rows(ORG_ADMIN, ORG)

    assert row.subscription.id == new_id
    assert row.names == ("Gina",)
    assert (row.blocked, row.last_status) == (0, None)
    async with uow.read() as tx:
        events = await tx.list_audit_events(Scope(everything=True))
    assert [e.event.detail for e in events if e.event.action == "reports.digest.manage"] == [
        "create:daily"
    ]


async def test_only_who_holds_the_permission_may_manage(engine: AsyncEngine) -> None:
    uow, gina, _wim = await _world(engine)

    with pytest.raises(DomainError) as refused:
        await _admin(uow).create(
            GROUP_ADMIN,
            ORG,
            group_slug=None,
            period=DigestPeriod.DAILY,
            weekday=0,
            month_day=1,
            recipients=[gina],
        )

    assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_a_colleague_who_may_not_read_the_reports_cannot_be_named(
    engine: AsyncEngine,
) -> None:
    uow, _gina, wim = await _world(engine)  # an agent has no reports

    with pytest.raises(DomainError) as refused:
        await _subscribe(uow, wim)

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "recipients",
    )


async def test_a_group_admin_cannot_be_named_for_the_whole_organisation(
    engine: AsyncEngine,
) -> None:
    uow, gina, _wim = await _world(engine)

    candidates = await _admin(uow).candidates(ORG_ADMIN, ORG, None)
    for_group = await _admin(uow).candidates(ORG_ADMIN, ORG, "alfa")

    assert {c.member.display_name for c in candidates} == {"Olga"}
    assert {c.member.display_name for c in for_group} == {"Olga", "Gina"}
    assert gina


async def test_the_most_a_organisation_may_have_is_enforced(engine: AsyncEngine) -> None:
    uow, gina, _wim = await _world(engine)
    await _subscribe(uow, gina, "alfa")

    with pytest.raises(DomainError) as refused:
        await _admin(uow, limit=1).create(
            ORG_ADMIN,
            ORG,
            group_slug="alfa",
            period=DigestPeriod.WEEKLY,
            weekday=2,
            month_day=1,
            recipients=[gina],
        )

    assert (refused.value.code, refused.value.field) == (ErrorCode.INVALID_CONFIGURATION, "limit")


async def test_a_subscription_is_deleted_only_in_its_own_organisation(engine: AsyncEngine) -> None:
    uow, gina, _wim = await _world(engine)
    new_id = await _subscribe(uow, gina, "alfa")
    other = principal("sub-other", "Otto", {Role.ORG_ADMIN}, organisations={"org-beta"})

    with pytest.raises(DomainError):
        await _admin(uow).delete(other, "org-beta", new_id)
    await _admin(uow).delete(ORG_ADMIN, ORG, new_id)

    assert await _admin(uow).rows(ORG_ADMIN, ORG) == []


async def test_a_due_digest_is_mailed_once_to_a_recipient_who_may_still_read(
    engine: AsyncEngine,
) -> None:
    uow, gina, _wim = await _world(engine)
    await _subscribe(uow, gina, "alfa")
    sender = RecordingSender()
    runner = _runner(uow, sender, {"sub-group": SECRET_ADDRESS})

    first = await runner.run()
    second = await runner.run()

    assert (first.sent, first.skipped, first.digests) == (1, 0, 1)
    assert second.digests == 0
    (message,) = sender.sent
    assert message.recipient == SECRET_ADDRESS
    assert message.attachments == ()
    assert message.subject == "subject nl"
    assert "https://planner.example/admin/groups/alfa/usage?from=2026-11-08&to=2026-11-08" in (
        message.text
    )
    async with uow.read() as tx:
        sub = (await tx.list_all_subscriptions())[0]
        assert await tx.has_digest_run(sub.id or 0, date(2026, 11, 9))


async def test_a_recipient_without_an_address_or_without_the_right_is_skipped(
    engine: AsyncEngine,
) -> None:
    uow, gina, _wim = await _world(engine)
    await _subscribe(uow, gina, "alfa")
    sender = RecordingSender()

    run = await _runner(uow, sender, {}).run()

    assert (run.sent, run.skipped) == (0, 1)
    assert sender.sent == []
    async with uow.read() as tx:
        sub = (await tx.list_all_subscriptions())[0]
        last = await tx.last_digest_run(sub.id or 0)
    assert last is not None
    assert last[1] == RunStatus.NO_RECIPIENT.value


async def test_a_digest_that_is_too_late_is_skipped_and_noted(engine: AsyncEngine) -> None:
    uow, gina, _wim = await _world(engine)
    await _subscribe(uow, gina, "alfa")
    sender = RecordingSender()

    run = await _runner(uow, sender, {"sub-group": SECRET_ADDRESS}, late_hours=1).run()

    assert (run.late, run.sent, run.digests) == (1, 0, 1)
    assert sender.sent == []
    async with uow.read() as tx:
        sub = (await tx.list_all_subscriptions())[0]
        last = await tx.last_digest_run(sub.id or 0)
    assert last is not None
    assert last[1] == RunStatus.SKIPPED_LATE.value


async def test_an_organisation_without_a_mail_channel_is_held_back_not_claimed(
    engine: AsyncEngine,
) -> None:
    uow, gina, _wim = await _world(engine)
    await _subscribe(uow, gina, "alfa")

    run = await _runner(uow, None, {"sub-group": SECRET_ADDRESS}).run()

    assert (run.no_channel, run.digests) == (1, 0)
    async with uow.read() as tx:
        sub = (await tx.list_all_subscriptions())[0]
        assert not await tx.has_digest_run(sub.id or 0, date(2026, 11, 9))


async def test_with_the_numbers_on_the_figures_come_from_the_suppressed_reports(
    engine: AsyncEngine,
) -> None:
    uow, gina, _wim = await _world(engine)
    await _subscribe(uow, gina, "alfa")
    async with uow.read() as tx:
        sub = (await tx.list_all_subscriptions())[0]
    hour = hour_of(NOW - timedelta(hours=20))
    async with uow.locked() as tx:
        for _ in range(8):
            await tx.record_usage(
                UsageFact(FactKind.BOOKING_CREATED, hour, sub.organisation_id, sub.group_id or 0)
            )
            await tx.record_usage(
                UsageFact(
                    FactKind.BOOKING_CANCELLED,
                    hour,
                    sub.organisation_id,
                    sub.group_id or 0,
                    outcome=UsageOutcome.CANCELLED_BY_PLANNER,
                )
            )
    clock = FixedClock(NOW)
    await UsageRollup(
        uow, clock, lambda: timedelta(minutes=15), lambda: 35, lambda: 25
    ).run_if_due()
    contents: list[DigestContent] = []

    def compose(content: DigestContent, locale: str) -> DigestMail:
        contents.append(content)
        return _compose(content, locale)

    runner = _runner(uow, RecordingSender(), {"sub-group": SECRET_ADDRESS}, numbers=True)
    runner._compose = compose  # noqa: SLF001
    await runner.run()

    (content,) = contents
    assert content.figures is not None
    assert content.figures.bookings == 8


async def test_who_has_no_address_is_shown_when_picking_and_when_listed(
    engine: AsyncEngine,
) -> None:
    uow, gina, _wim = await _world(engine)
    await _subscribe(uow, gina, "alfa")

    candidates = await _admin(uow).candidates(ORG_ADMIN, ORG, "alfa")
    (row,) = await _admin(uow).rows(ORG_ADMIN, ORG)

    assert {c.member.display_name: c.has_address for c in candidates} == {
        "Olga": True,
        "Gina": False,
    }
    assert (row.blocked, row.no_address) == (0, 1)
