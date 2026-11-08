"""Routing and task ids in their new places, old columns kept in step (ADR 0032, ADR 0116)."""

from dataclasses import replace

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.jobs import SqlJobQueue
from app.adapters.db.models import BookingRow, GroupContactCenterRow, GroupRow, JobRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.contact_center import ContactCenterKind
from app.domain.job_plan import JobType
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, webex_routing

ROUTING = webex_routing("openplanner@example.biz", "openplanner-video", "team-1")


async def _columns(engine: AsyncEngine) -> tuple[object, ...]:
    async with engine.connect() as connection:
        old = (
            await connection.execute(
                select(
                    GroupRow.wxcc_business_address,
                    GroupRow.wxcc_channel,
                    GroupRow.wxcc_routing_value,
                )
            )
        ).one()
        new = (
            await connection.execute(
                select(
                    GroupContactCenterRow.kind,
                    GroupContactCenterRow.routing_value,
                    GroupContactCenterRow.attributes,
                )
            )
        ).one_or_none()
    return tuple(old), tuple(new) if new else None


async def test_a_saved_routing_is_written_to_both_places_and_read_back(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        assert group.contact_center is None
        await tx.save_group(replace(group, contact_center=ROUTING))

    async with uow.read() as tx:
        stored = await tx.get_group("aangifte")
        summary = await tx.get_group_summary("aangifte")
    assert stored is not None
    assert stored.contact_center == ROUTING
    assert summary is not None
    assert summary.group.contact_center == ROUTING
    assert await _columns(engine) == (
        ("openplanner@example.biz", "openplanner-video", "team-1"),
        (
            "webex_cc",
            "team-1",
            '{"business_address":"openplanner@example.biz","channel":"openplanner-video"}',
        ),
    )


async def test_removing_the_routing_empties_both_places(engine: AsyncEngine) -> None:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        await tx.save_group(replace(group, contact_center=ROUTING))
    async with uow.locked() as tx:
        group = await tx.get_group("aangifte")
        assert group is not None
        await tx.save_group(replace(group, contact_center=None))

    assert await _columns(engine) == ((None, None, None), None)
    async with uow.read() as tx:
        stored = await tx.get_group("aangifte")
    assert stored is not None
    assert stored.contact_center is None


async def test_a_group_written_by_the_previous_minor_reads_from_the_old_columns(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, slug="aangifte")
    async with engine.begin() as connection:
        await connection.execute(
            update(GroupRow).values(
                wxcc_business_address="openplanner@example.biz", wxcc_channel="openplanner-video"
            )
        )

    async with SqlUnitOfWork(engine).read() as tx:
        group = await tx.get_group("aangifte")
    assert group is not None
    assert group.contact_center == webex_routing("openplanner@example.biz", "openplanner-video")
    assert group.dispatch_enabled


async def test_a_task_is_stored_in_both_columns_and_an_old_one_still_found(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    first = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    booking_id = first.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_task_id(booking_id, "task-new", ContactCenterKind.WEBEX_CC)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(
                    BookingRow.contact_center_task_id,
                    BookingRow.contact_center_kind,
                    BookingRow.wxcc_task_id,
                ).where(BookingRow.id == booking_id)
            )
        ).one()
    assert tuple(row) == ("task-new", "webex_cc", "task-new")

    # A row the previous minor wrote has the old column only.
    async with engine.begin() as connection:
        await connection.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(contact_center_task_id=None, contact_center_kind=None, wxcc_task_id="t-old")
        )
    async with uow.read() as tx:
        found = await tx.get_booking_by_task("t-old")
    assert found is not None
    assert (found.contact_center_task_id, found.contact_center_kind) == (
        "t-old",
        ContactCenterKind.WEBEX_CC,
    )


async def test_a_dispatch_job_of_the_previous_minor_is_claimed_as_the_new_type(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, slug="aangifte")
    uow = SqlUnitOfWork(engine)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY))
    booking_id = receipt.booking.id or 0
    async with engine.begin() as connection:
        await connection.execute(
            JobRow.__table__.insert().values(
                booking_id=booking_id, type="dispatch_wxcc_task", run_at=NOW, status="pending"
            )
        )
    async with uow.locked() as tx:
        assert await tx.claim_booking_job(booking_id, JobType.DISPATCH_CONTACT_CENTER_TASK, NOW)
        await tx.finish_booking_job(booking_id, JobType.DISPATCH_CONTACT_CENTER_TASK, done=False)

    [job] = await SqlJobQueue(engine).claim_due(NOW, {JobType.DISPATCH_CONTACT_CENTER_TASK}, set())
    assert job.type is JobType.DISPATCH_CONTACT_CENTER_TASK
