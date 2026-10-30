"""Handing the stored pair to the proxy through the shared directory (ADR 0066)."""

import stat
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.certificate_delivery import FileCertificateDelivery
from app.adapters.crypto import SecretBox
from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.own_certificate import OwnCertificate
from app.domain.tls import Certificate
from tests.support.database import migrated_database
from tests.support.domain import NOW

HOST = "afspraak.example.nl"
FINGERPRINT = "ab" * 32
CHAIN = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nMIGH\n-----END PRIVATE KEY-----\n"


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "delivery")


@pytest.fixture
async def uow(database_url: str) -> AsyncIterator[SqlUnitOfWork]:
    engine: AsyncEngine = create_engine(database_url)
    yield SqlUnitOfWork(engine, SecretBox(b"s" * 32))
    await engine.dispose()


async def store(uow: SqlUnitOfWork, fingerprint: str = FINGERPRINT, key: str = KEY) -> None:
    certificate = Certificate(
        HOST, "Demo", NOW - timedelta(days=1), NOW + timedelta(days=9), (HOST,)
    )
    async with uow.locked() as tx:
        await tx.save_own_certificate(OwnCertificate(certificate, fingerprint, CHAIN, NOW), key)


async def test_publishing_writes_the_three_files_serial_last_and_the_key_private(
    uow: SqlUnitOfWork, tmp_path: Path
) -> None:
    directory = tmp_path / "portal-certs"
    await store(uow)

    await FileCertificateDelivery(uow, directory).publish(FINGERPRINT)

    assert (directory / "fullchain.pem").read_text() == CHAIN
    assert (directory / "privkey.pem").read_text() == KEY
    assert (directory / "serial").read_text() == FINGERPRINT + "\n"
    assert stat.S_IMODE((directory / "privkey.pem").stat().st_mode) == 0o600
    assert not list(directory.glob("*.tmp"))


async def test_the_same_fingerprint_is_not_written_twice(
    uow: SqlUnitOfWork, tmp_path: Path
) -> None:
    delivery = FileCertificateDelivery(uow, tmp_path)
    await store(uow)
    await delivery.publish(FINGERPRINT)
    (tmp_path / "privkey.pem").write_text("left alone")

    await delivery.publish(FINGERPRINT)

    assert (tmp_path / "privkey.pem").read_text() == "left alone"


async def test_a_replaced_pair_is_written_over_the_old_one(
    uow: SqlUnitOfWork, tmp_path: Path
) -> None:
    delivery = FileCertificateDelivery(uow, tmp_path)
    await store(uow)
    await delivery.publish(FINGERPRINT)
    await store(uow, "cd" * 32, "new key")

    await delivery.publish("cd" * 32)

    assert (tmp_path / "privkey.pem").read_text() == "new key"
    assert (tmp_path / "serial").read_text().strip() == "cd" * 32


async def test_nothing_is_written_when_the_store_does_not_hold_that_pair(
    uow: SqlUnitOfWork, tmp_path: Path
) -> None:
    await FileCertificateDelivery(uow, tmp_path / "certs").publish(FINGERPRINT)

    assert not (tmp_path / "certs").exists()


async def test_withdrawing_removes_the_serial_and_the_pair(
    uow: SqlUnitOfWork, tmp_path: Path
) -> None:
    directory = tmp_path / "portal-certs"
    delivery = FileCertificateDelivery(uow, directory)
    await store(uow)
    await delivery.publish(FINGERPRINT)

    await delivery.withdraw()
    await delivery.withdraw()  # nothing there is fine

    assert sorted(p.name for p in directory.iterdir()) == []


async def test_a_volume_that_cannot_be_written_is_logged_and_never_raises(
    uow: SqlUnitOfWork, tmp_path: Path
) -> None:
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("a file where the directory should be")
    await store(uow)

    await FileCertificateDelivery(uow, blocked / "portal-certs").publish(FINGERPRINT)
    await FileCertificateDelivery(uow, blocked / "portal-certs").withdraw()

    assert blocked.read_text() == "a file where the directory should be"
