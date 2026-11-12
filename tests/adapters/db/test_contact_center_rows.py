"""Routing and task ids in their new places, old columns kept in step (ADR 0032, ADR 0116)."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.models import BookingRow, GroupContactCenterRow, GroupRow, JobRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.contact_center import ContactCenterKind, ContactCenterTaskRef
from app.domain.job_plan import JobType, PlannedJob
from app.domain.services import BookingService
from tests.adapters.db.conftest import seed_group
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, webex_routing

WEBEX_CC = ContactCenterKind.WEBEX_CC
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
    await seed_group(engine, slug="balie")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("balie")
        assert group is not None
        assert group.contact_center is None
        await tx.save_group(replace(group, contact_center=ROUTING))

    async with uow.read() as tx:
        stored = await tx.get_group("balie")
        summary = await tx.get_group_summary("balie")
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
    await seed_group(engine, slug="balie")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("balie")
        assert group is not None
        await tx.save_group(replace(group, contact_center=ROUTING))
    async with uow.locked() as tx:
        group = await tx.get_group("balie")
        assert group is not None
        await tx.save_group(replace(group, contact_center=None))

    assert await _columns(engine) == ((None, None, None), None)
    async with uow.read() as tx:
        stored = await tx.get_group("balie")
    assert stored is not None
    assert stored.contact_center is None


async def test_a_group_written_by_the_previous_minor_reads_from_the_old_columns(
    engine: AsyncEngine,
) -> None:
    await seed_group(engine, slug="balie")
    async with engine.begin() as connection:
        await connection.execute(
            update(GroupRow).values(
                wxcc_business_address="openplanner@example.biz", wxcc_channel="openplanner-video"
            )
        )

    async with SqlUnitOfWork(engine).read() as tx:
        group = await tx.get_group("balie")
    assert group is not None
    assert group.contact_center == webex_routing("openplanner@example.biz", "openplanner-video")
    assert group.dispatch_enabled


async def test_an_edit_by_the_previous_image_to_the_old_columns_wins_over_the_copy(
    engine: AsyncEngine,
) -> None:
    """ADR 0116 §5: for WxCC the old columns are the source of truth in this minor."""
    await seed_group(engine, slug="balie")
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        group = await tx.get_group("balie")
        assert group is not None
        await tx.save_group(replace(group, contact_center=ROUTING))
    # The previous image edits the group: it only knows the old columns.
    async with engine.begin() as connection:
        await connection.execute(
            update(GroupRow).values(wxcc_channel="other-channel", wxcc_routing_value=None)
        )

    async with uow.read() as tx:
        stored = await tx.get_group("balie")
    assert stored is not None
    assert stored.contact_center == webex_routing("openplanner@example.biz", "other-channel")


async def _booking_with_task(engine: AsyncEngine) -> tuple[SqlUnitOfWork, int]:
    await seed_group(engine, slug="balie")
    uow = SqlUnitOfWork(engine)
    receipt = await book(BookingService(uow, FixedClock(NOW), TEST_TOKEN_KEY, treatment_buffer=0))
    booking_id = receipt.booking.id or 0
    async with uow.locked() as tx:
        await tx.save_task(booking_id, ContactCenterTaskRef("task-new", WEBEX_CC))
    return uow, booking_id


async def test_a_task_is_stored_in_both_columns_and_an_old_one_still_found(
    engine: AsyncEngine,
) -> None:
    uow, booking_id = await _booking_with_task(engine)
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
        found = await tx.get_booking_by_task("t-old", WEBEX_CC)
    assert found is not None
    assert found.contact_center_task == ContactCenterTaskRef("t-old", WEBEX_CC)


async def test_a_purge_by_the_previous_image_leaves_no_task_behind(engine: AsyncEngine) -> None:
    """The old image empties ``wxcc_task_id`` only; the WxCC copy must not bring the task back."""
    uow, booking_id = await _booking_with_task(engine)
    async with engine.begin() as connection:
        await connection.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(
                wxcc_task_id=None,
                first_name=None,
                last_name=None,
                email=None,
                phone=None,
                guest_join_url=None,
                host_join_url=None,
                pii_purged_at=NOW,
            )
        )

    async with uow.read() as tx:
        booking = await tx.get_booking(booking_id)
        found = await tx.get_booking_by_task("task-new", WEBEX_CC)
        rollable = await tx.find_rollable(NOW + timedelta(days=1), 0, 10)
    assert booking is not None
    assert booking.contact_center_task is None
    assert found is None
    [row] = rollable
    assert not row.holds_data  # the stale copy does not keep the row from its rollup


async def test_for_a_wxcc_task_the_new_column_never_wins_over_the_old_one(
    engine: AsyncEngine,
) -> None:
    """Old column NULL or different, new column filled, kind ``webex_cc``: the old one counts."""
    uow, booking_id = await _booking_with_task(engine)
    async with engine.begin() as connection:
        await connection.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(contact_center_task_id="t-other", wxcc_task_id="t-wxcc")
        )
    async with uow.read() as tx:
        # webex_cc: the old column wins
        assert await tx.get_booking_by_task("t-other", WEBEX_CC) is None
        found = await tx.get_booking_by_task("t-wxcc", WEBEX_CC)
    assert found is not None
    assert found.contact_center_task == ContactCenterTaskRef("t-wxcc", WEBEX_CC)


async def test_a_dispatch_job_keeps_the_value_the_previous_image_reads(
    engine: AsyncEngine,
) -> None:
    uow, booking_id = await _booking_with_task(engine)
    async with engine.begin() as connection:
        await connection.execute(
            JobRow.__table__.insert().values(
                booking_id=booking_id,
                type="dispatch_contact_center_task",  # what the next minor writes
                run_at=NOW,
                status="pending",
            )
        )
    async with uow.read() as tx:
        jobs = await tx.get_booking_jobs(booking_id)
    assert JobType.DISPATCH_CONTACT_CENTER_TASK in {j.type for j in jobs}  # still reads
    async with uow.locked() as tx:
        await tx.add_jobs(booking_id, [PlannedJob(JobType.DISPATCH_CONTACT_CENTER_TASK, NOW, None)])
    async with engine.connect() as connection:
        types = set(
            (await connection.execute(select(JobRow.type).where(JobRow.booking_id == booking_id)))
            .scalars()
            .all()
        )
    assert "dispatch_wxcc_task" in types  # this image writes the old value (ADR 0116 §5)


async def test_a_task_id_is_found_only_at_its_own_platform(engine: AsyncEngine) -> None:
    """ADR 0032 point 6: a row whose task is at another platform is not a WxCC task."""
    uow, booking_id = await _booking_with_task(engine)
    async with engine.begin() as connection:
        await connection.execute(
            update(BookingRow)
            .where(BookingRow.id == booking_id)
            .values(
                wxcc_task_id=None, contact_center_task_id="t-1", contact_center_kind="elsewhere"
            )
        )
    async with uow.read() as tx:
        assert await tx.get_booking_by_task("t-1", WEBEX_CC) is None
