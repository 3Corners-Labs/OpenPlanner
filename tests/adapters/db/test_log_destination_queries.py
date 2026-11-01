"""SQL queries of the log destinations: the sealed credential, uniqueness, status (ADR 0085)."""

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.log_destinations import LogDestinationCredentialStore, secret_name
from app.adapters.db.models import LogDestinationRow, LogDestinationStatusRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import Category
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.log_destinations import DestinationKind, DestinationStatus, LogDestination, State
from tests.support.domain import NOW

BOX = SecretBox(b"k" * 32)
EVERYTHING = Scope(everything=True)


def destination(name: str = "Splunk", **changes: object) -> LogDestination:
    values: dict[str, object] = {
        "id": None,
        "name": name,
        "kind": DestinationKind.SPLUNK_HEC,
        "address": "https://splunk.example.org:8088/services/collector/event",
        "index": "openplanner",
        "source": "",
        "min_level": "info",
        "categories": frozenset({Category.AUDIT, Category.SYSTEM}),
        "include_diagnostic": True,
        "include_trace": False,
        "enabled": True,
        "secret_set": True,
        "ca_pem": "",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return LogDestination(**values)  # type: ignore[arg-type]


@pytest.fixture
def uow(engine: AsyncEngine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine, BOX)


async def test_a_destination_round_trips_and_its_credential_is_sealed(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    async with uow.locked() as tx:
        stored = await tx.add_log_destination(destination(), "hec-token")

    async with uow.read() as tx:
        found = await tx.get_log_destination(stored.id or 0)
        listed = await tx.list_log_destinations(EVERYTHING)
    assert found == stored
    assert listed == [stored]
    async with uow.read() as tx:  # destinations belong to the installation, not to an organisation
        assert await tx.list_log_destinations(Scope(organisations=frozenset({"demo"}))) == []
        assert await tx.list_log_destination_status(Scope(organisations=frozenset({"demo"}))) == []
    assert stored.secret_set
    assert stored.categories == {Category.AUDIT, Category.SYSTEM}
    async with AsyncSession(engine) as session:
        row = (await session.scalars(select(LogDestinationRow))).one()
    assert row.secret_ciphertext is not None
    assert b"hec-token" not in row.secret_ciphertext
    opened = BOX.open(secret_name(stored.id or 0), row.secret_nonce, row.secret_ciphertext)
    assert opened == b"hec-token"


async def test_a_ciphertext_cannot_be_moved_to_another_destination(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    async with uow.locked() as tx:
        first = await tx.add_log_destination(destination("One"), "token-one")
        second = await tx.add_log_destination(destination("Two"), "token-two")
    async with AsyncSession(engine) as session:
        rows = {r.id: r for r in (await session.scalars(select(LogDestinationRow))).all()}

    stolen = rows[first.id or 0]
    assert stolen.secret_nonce is not None
    assert stolen.secret_ciphertext is not None
    with pytest.raises(InvalidTag):
        BOX.open(secret_name(second.id or 0), stolen.secret_nonce, stolen.secret_ciphertext)


async def test_a_blank_secret_keeps_the_stored_one_and_a_new_one_replaces_it(
    uow: SqlUnitOfWork,
) -> None:
    store = LogDestinationCredentialStore(uow._engine, BOX)  # noqa: SLF001
    async with uow.locked() as tx:
        stored = await tx.add_log_destination(destination(), "first")
    async with uow.locked() as tx:
        await tx.save_log_destination(destination(id=stored.id, min_level="error"), None)
    assert (await store.one(stored.id or 0)).secret == "first"  # type: ignore[union-attr]
    async with uow.locked() as tx:
        await tx.save_log_destination(destination(id=stored.id), "second")

    target = await store.one(stored.id or 0)

    assert target is not None
    assert (target.secret, target.destination.min_level) == ("second", "info")


async def test_names_are_unique(uow: SqlUnitOfWork) -> None:
    async with uow.locked() as tx:
        one = await tx.add_log_destination(destination("One"), "t")
        await tx.add_log_destination(destination("Two"), "t")
    with pytest.raises(DomainError) as caught:
        async with uow.locked() as tx:
            await tx.add_log_destination(destination("One"), "t")
    assert caught.value.code is ErrorCode.LOG_DESTINATION_NAME_TAKEN
    with pytest.raises(DomainError):
        async with uow.locked() as tx:
            await tx.save_log_destination(destination("Two", id=one.id), None)
    async with uow.locked() as tx:
        await tx.save_log_destination(destination("One", id=one.id, enabled=False), None)


async def test_a_credential_without_a_settings_key_is_refused(engine: AsyncEngine) -> None:
    keyless = SqlUnitOfWork(engine, None)

    with pytest.raises(DomainError) as caught:
        async with keyless.locked() as tx:
            await tx.add_log_destination(destination(), "token")

    assert caught.value.code is ErrorCode.INTEGRATION_NOT_READY
    async with keyless.locked() as tx:  # a destination without a credential still works
        await tx.add_log_destination(
            destination(kind=DestinationKind.SYSLOG, secret_set=False), None
        )


async def test_the_store_opens_enabled_destinations_and_flags_unreadable_ones(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    async with uow.locked() as tx:
        await tx.add_log_destination(destination("On"), "token")
        await tx.add_log_destination(destination("Off", enabled=False), "token")
        await tx.add_log_destination(
            destination("Syslog", kind=DestinationKind.SYSLOG, secret_set=False), None
        )

    readable = await LogDestinationCredentialStore(engine, BOX).enabled()
    wrong_key = await LogDestinationCredentialStore(engine, SecretBox(b"x" * 32)).enabled()
    no_key = await LogDestinationCredentialStore(engine, None).enabled()

    assert [(t.destination.name, t.secret, t.unreadable) for t in readable] == [
        ("On", "token", False),
        ("Syslog", None, False),
    ]
    assert [(t.destination.name, t.secret, t.unreadable) for t in wrong_key] == [
        ("On", None, True),
        ("Syslog", None, False),
    ]
    assert [t.unreadable for t in no_key] == [True, False]


async def test_status_is_kept_per_process_and_goes_with_the_destination(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    async with uow.locked() as tx:
        stored = await tx.add_log_destination(destination(), "token")
    destination_id = stored.id or 0
    web = DestinationStatus(destination_id, "web", State.OK, sent=12, last_ok_at=NOW)
    worker = DestinationStatus(
        destination_id, "worker", State.FAILING, failed=3, dropped=7, last_error="tls"
    )

    async with uow.locked() as tx:
        await tx.save_log_destination_status(web, NOW)
        await tx.save_log_destination_status(worker, NOW)
        await tx.save_log_destination_status(web.__class__(999, "web"), NOW)  # unknown: skipped
    async with uow.locked() as tx:
        await tx.save_log_destination_status(
            DestinationStatus(destination_id, "web", State.OK, sent=20, last_ok_at=NOW), NOW
        )

    async with uow.read() as tx:
        reported = await tx.list_log_destination_status(EVERYTHING)
    assert [(s.service, s.state, s.sent, s.failed, s.dropped, s.last_error) for s in reported] == [
        ("web", State.OK, 20, 0, 0, ""),
        ("worker", State.FAILING, 0, 3, 7, "tls"),
    ]

    async with uow.locked() as tx:
        assert await tx.delete_log_destination(destination_id) is True
        assert await tx.delete_log_destination(destination_id) is False
    async with AsyncSession(engine) as session:
        assert (await session.scalars(select(LogDestinationStatusRow))).all() == []
        assert (await session.scalars(select(LogDestinationRow))).all() == []
