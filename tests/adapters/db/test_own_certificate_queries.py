"""Storage of the own certificate: the chain and public key, never a private key (ADR 0070)."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import OwnCertificateRow
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.own_certificate import OwnCertificate
from app.domain.tls import Certificate
from tests.support.domain import NOW

HOST = "afspraak.example.nl"


def stored(fingerprint: str = "ab" * 32, public_key: bytes = b"spki-1") -> OwnCertificate:
    return OwnCertificate(
        Certificate(
            HOST, "Gemeente Demo", NOW - timedelta(days=1), NOW + timedelta(days=90), (HOST,)
        ),
        fingerprint,
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        public_key,
        NOW,
    )


async def test_a_fresh_database_holds_no_certificate(engine: AsyncEngine) -> None:
    async with SqlUnitOfWork(engine).read() as tx:
        assert await tx.get_own_certificate() is None
        assert await tx.delete_own_certificate() is False


async def test_the_chain_is_stored_and_read_back(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored())

    async with uow.read() as tx:
        assert await tx.get_own_certificate() == stored()


async def test_a_new_upload_replaces_the_single_row(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored("11" * 32))
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored("22" * 32, b"spki-2"))

    async with uow.read() as tx:
        current = await tx.get_own_certificate()
    assert current is not None
    assert (current.fingerprint, current.public_key) == ("22" * 32, b"spki-2")
    async with AsyncSession(engine) as session:
        assert len((await session.scalars(select(OwnCertificateRow))).all()) == 1


async def test_deleting_removes_the_row(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored())

    async with uow.locked() as tx:
        assert await tx.delete_own_certificate() is True
    async with uow.read() as tx:
        assert await tx.get_own_certificate() is None


def test_the_table_has_no_column_for_a_private_key() -> None:
    columns = {column.name for column in OwnCertificateRow.__table__.columns}

    assert "public_key" in columns
    assert not {name for name in columns if "private" in name or "ciphertext" in name}
