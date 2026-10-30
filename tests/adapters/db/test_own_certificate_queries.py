"""Storage of the own certificate: the chain plain, the key sealed (ADR 0066, ADR 0008)."""

from datetime import timedelta

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import OwnCertificateRow
from app.adapters.db.uow import SqlTransaction, SqlUnitOfWork
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import OwnCertificate
from app.domain.tls import Certificate
from tests.support.domain import NOW

HOST = "afspraak.example.nl"
KEY_PEM = (
    "-----BEGIN PRIVATE KEY-----\nMIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEH\n-----END PRIVATE KEY-----\n"
)
BOX = SecretBox(b"s" * 32)


def stored(fingerprint: str = "ab" * 32) -> OwnCertificate:
    return OwnCertificate(
        Certificate(
            HOST, "Gemeente Demo", NOW - timedelta(days=1), NOW + timedelta(days=90), (HOST,)
        ),
        fingerprint,
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        NOW,
    )


async def test_a_fresh_database_holds_no_certificate(engine: AsyncEngine) -> None:
    async with SqlUnitOfWork(engine, BOX).read() as tx:
        assert await tx.get_own_certificate() is None
        assert await tx.delete_own_certificate() is False


async def test_the_pair_is_stored_read_back_and_the_key_opened_only_here(
    engine: AsyncEngine,
) -> None:
    uow = SqlUnitOfWork(engine, BOX)
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored(), KEY_PEM)

    async with uow.read() as tx:
        sql_tx: SqlTransaction = tx
        assert await tx.get_own_certificate() == stored()
        assert await sql_tx.own_certificate_key() == KEY_PEM

    async with AsyncSession(engine) as session:
        row = (await session.scalars(select(OwnCertificateRow))).one()
    assert KEY_PEM.encode() not in row.key_ciphertext
    assert row.chain_pem.startswith("-----BEGIN CERTIFICATE-----")


async def test_a_new_upload_replaces_the_single_row(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine, BOX)
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored("11" * 32), KEY_PEM)
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored("22" * 32), "other key")

    async with uow.read() as tx:
        sql_tx: SqlTransaction = tx
        current = await tx.get_own_certificate()
        assert current is not None
        assert current.fingerprint == "22" * 32
        assert await sql_tx.own_certificate_key() == "other key"
    async with AsyncSession(engine) as session:
        assert len((await session.scalars(select(OwnCertificateRow))).all()) == 1


async def test_deleting_removes_the_key_with_the_row(engine: AsyncEngine) -> None:
    uow = SqlUnitOfWork(engine, BOX)
    async with uow.locked() as tx:
        await tx.save_own_certificate(stored(), KEY_PEM)

    async with uow.locked() as tx:
        assert await tx.delete_own_certificate() is True
    async with uow.read() as tx:
        sql_tx: SqlTransaction = tx
        assert await tx.get_own_certificate() is None
        assert await sql_tx.own_certificate_key() is None


async def test_without_a_settings_key_nothing_is_stored(engine: AsyncEngine) -> None:
    async with SqlUnitOfWork(engine).locked() as tx:
        with pytest.raises(DomainError) as refused:
            await tx.save_own_certificate(stored(), KEY_PEM)

    assert refused.value.code is ErrorCode.SETTING_NO_KEY


async def test_another_settings_key_cannot_open_the_stored_key(engine: AsyncEngine) -> None:
    async with SqlUnitOfWork(engine, BOX).locked() as tx:
        await tx.save_own_certificate(stored(), KEY_PEM)

    async with SqlUnitOfWork(engine, SecretBox(b"t" * 32)).read() as tx:
        sql_tx: SqlTransaction = tx
        assert await tx.get_own_certificate() is not None  # the public data still reads
        with pytest.raises(InvalidTag):
            await sql_tx.own_certificate_key()
