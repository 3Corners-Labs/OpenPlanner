"""Staff status, erasure and the erased actor in the audit log, in SQLite (ADR 0046 step 2)."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Role, Scope
from app.domain.staff import Membership, RefusedEntitlement, StaffFilter, StaffMember, StaffStatus
from tests.support.domain import NOW

DEMO = Membership("org_admin", "demo")


def _person(subject: str, name: str = "Gina") -> StaffMember:
    return StaffMember(subject, name, frozenset({Role.ORG_ADMIN}), frozenset({DEMO}), NOW, NOW, NOW)


async def test_status_is_stored_and_survives_a_save(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(_person("sub-1"))
        await tx.save_staff(_person("sub-2", "Olaf"))

    async with uow.locked() as tx:
        await tx.set_staff_status("sub-1", StaffStatus.SUSPENDED, NOW)
        await tx.set_staff_status("nobody", StaffStatus.SUSPENDED, NOW)  # unknown: ignored
    async with uow.read() as tx:
        suspended = await tx.get_staff("sub-1")
        active = await tx.get_staff("sub-2")

    assert suspended is not None
    assert (suspended.status, suspended.status_at) == (StaffStatus.SUSPENDED, NOW)
    assert active is not None
    assert (active.status, active.status_at) == (StaffStatus.ACTIVE, None)
    async with uow.locked() as tx:
        await tx.save_staff(replace(suspended, status=StaffStatus.ACTIVE, status_at=NOW))
    async with uow.read() as tx:
        again = await tx.get_staff("sub-1")
    assert again is not None
    assert again.status is StaffStatus.ACTIVE


async def test_erasing_removes_the_personal_rows_and_keeps_subject_and_times(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    refused = RefusedEntitlement("op:x", "unknown", "demo", NOW)
    async with uow.locked() as tx:
        await tx.save_staff(replace(_person("sub-1"), refused=(refused,)))
    later = NOW + timedelta(days=1)

    async with uow.locked() as tx:
        await tx.erase_staff("sub-1", later)
        await tx.erase_staff("sub-1", later + timedelta(days=1))  # a second run changes nothing
        await tx.erase_staff("nobody", later)

    async with uow.read() as tx:
        erased = await tx.get_staff("sub-1")
        everyone = await tx.list_staff(Scope(everything=True), StaffFilter())
        in_demo = await tx.list_staff(Scope(organisations=frozenset({"demo"})), StaffFilter())
    assert erased is not None
    assert (erased.display_name, erased.roles, erased.memberships, erased.refused) == (
        "",
        frozenset(),
        frozenset(),
        (),
    )
    assert (erased.status, erased.status_at, erased.last_login_at) == (
        StaffStatus.ERASED,
        later,
        NOW,
    )
    assert [m.subject for m in everyone] == ["sub-1"]  # only the superuser's view lists it
    assert in_demo == []  # no membership left, so no organisation sees it


async def test_an_erased_actor_is_flagged_and_has_no_name_in_the_audit_log(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.add_audit_event(AuditEvent(NOW, "sub-1", "admin.login", Outcome.SUCCESS))
        await tx.save_staff(_person("sub-1", "Gina Groep"))
    async with uow.read() as tx:
        before = await tx.list_audit_events(Scope(everything=True))
    async with uow.locked() as tx:
        await tx.erase_staff("sub-1", NOW)

    async with uow.read() as tx:
        after = await tx.list_audit_events(Scope(everything=True))

    assert [(e.actor_name, e.actor_erased) for e in before] == [("Gina Groep", False)]
    assert [(e.actor_name, e.actor_erased) for e in after] == [(None, True)]


async def test_the_address_is_stored_replaced_and_erased_with_the_person(
    engine: AsyncEngine,
) -> None:
    # ADR 0117 step 10b
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_staff(replace(_person("sub-1"), email="gina@voorbeeld.nl", email_at=NOW))
    async with uow.read() as tx:
        stored = await tx.get_staff("sub-1")
    assert stored is not None
    assert stored.email == "gina@voorbeeld.nl"

    async with uow.locked() as tx:
        await tx.save_staff(_person("sub-1"))  # a later login with no verified address
    async with uow.read() as tx:
        cleared = await tx.get_staff("sub-1")
    assert cleared is not None
    assert cleared.email is None

    async with uow.locked() as tx:
        await tx.save_staff(replace(_person("sub-1"), email="gina@voorbeeld.nl", email_at=NOW))
        await tx.erase_staff("sub-1", NOW)
    async with uow.read() as tx:
        erased = await tx.get_staff("sub-1")
    assert erased is not None
    assert (erased.email, erased.email_at) == (None, None)
