"""SQLite configuration packages (ADR 0089), read for one release as documents (ADR 0099)."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.configuration_context import document_service
from app.adapters.crypto import SecretBox
from app.adapters.db.configuration_package import (
    SqliteConfigurationTransfer,
    environment_values,
    inspect_package,
)
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.engine import create_engine
from app.adapters.db.models import Base, StaffRow
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.configuration_document import Mode, Scope
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.configuration_package import CONFIGURATION_TABLES, STATE_TABLES, VERSION_TABLE
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.identity_providers import FakeDiscoveryFetcher
from tests.support.configuration_package import NOW, PASSPHRASE, SOURCE_BOX, database_path
from tests.support.database import migrated_database
from tests.support.legacy_package import export_package
from tests.support.principals import principal
from tests.support.seed_rows import seed_group

TARGET_BOX = SecretBox(b"t" * 32)
ENVIRONMENT = {"OIDC_DISCOVERY_URL": "https://idp.example/.well-known/openid-configuration"}
HOST = Actor.host()


async def _source(engine: AsyncEngine) -> None:
    """A configured installation with a sealed secret, a staff record and a slot."""
    await seed_demo(engine)
    await CredentialStore(engine, SOURCE_BOX).put("webex_service_app", {"token": "secret"}, NOW)
    async with AsyncSession(engine) as session, session.begin():
        session.add(
            StaffRow(
                subject="sub-1",
                display_name="Sanne",
                roles="superuser",
                first_seen_at=NOW,
                last_login_at=NOW,
                last_seen_at=NOW,
            )
        )


def _package(database: Path, tmp_path: Path, **overrides: object) -> bytes:
    options: dict[str, object] = {
        "created_at": NOW,
        "installation": "bron",
        "app_version": "1.2.3",
        "environment": ENVIRONMENT,
        "work_dir": tmp_path / "work",
    }
    options.update(overrides)
    return export_package(database, SOURCE_BOX, PASSPHRASE, **options)  # type: ignore[arg-type]


def _documents(engine: AsyncEngine) -> ConfigurationDocumentService:
    # The demo seed gives every service its default flows, published at the real time of the run;
    # a document may not publish in the future, so the clock is a little after that.
    clock = FixedClock(datetime.now(UTC) + timedelta(hours=1))
    uow = SqlUnitOfWork(engine, TARGET_BOX)
    return document_service(
        uow,
        clock,
        AdminService(uow, clock),
        settings=lambda: Settings(_env_file=None, database_url="sqlite+aiosqlite:///x"),  # type: ignore[call-arg]
        discovery=FakeDiscoveryFetcher(),
        locked=frozenset,
    )


def test_every_table_of_the_model_is_configuration_or_state() -> None:
    tables = set(Base.metadata.tables)

    assert tables == CONFIGURATION_TABLES | STATE_TABLES, (
        "classify every table in app/domain/configuration_package.py"
    )
    assert not CONFIGURATION_TABLES & STATE_TABLES
    assert VERSION_TABLE not in tables


def test_every_sealed_column_of_a_configuration_table_is_resealed() -> None:
    from app.adapters.db.sealed import SEALED as _SEALED

    sealed = {
        (table.name, column.name)
        for table in Base.metadata.sorted_tables
        if table.name in CONFIGURATION_TABLES
        for column in table.columns
        if column.name.endswith("ciphertext")
    }

    assert sealed == {(table, cipher) for table, _, cipher, _ in _SEALED}, (
        "a sealed column needs an entry in _SEALED, or its secret is unreadable after a read"
    )


async def test_a_package_is_inspected_with_its_passphrase_and_not_without(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)
    package = _package(database_path(database_url), tmp_path)

    summary = inspect_package(package, PASSPHRASE, tmp_path / "work")

    assert package.startswith(b"SQLite format 3\x00")
    assert b"Sanne" not in package  # staff is state, vacuumed away
    assert summary.counts["organisations"] == 2
    assert summary.secrets == 1
    assert summary.environment == ("OIDC_DISCOVERY_URL",)
    assert environment_values(package, PASSPHRASE, tmp_path / "work") == ENVIRONMENT
    with pytest.raises(DomainError) as refused:
        inspect_package(package, "not the passphrase", tmp_path / "work")
    assert refused.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_WRONG


async def test_a_package_secret_is_sealed_under_the_passphrase_not_the_installation_key(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)
    package = _package(database_path(database_url), tmp_path)

    file = tmp_path / "p.sqlite"
    file.write_bytes(package)
    with closing(sqlite3.connect(file)) as db:
        nonce, ciphertext = db.execute(
            "SELECT nonce, ciphertext FROM integration_credentials"
        ).fetchone()
    with pytest.raises(InvalidTag):
        SOURCE_BOX.open("webex_service_app", nonce, ciphertext)


async def test_a_package_becomes_a_replacing_document_of_the_platform(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)
    package = _package(database_path(database_url), tmp_path)
    transfer = SqliteConfigurationTransfer(database_path(database_url), TARGET_BOX, now=lambda: NOW)

    document, summary = await transfer.to_document(package, PASSPHRASE)

    assert document.mode is Mode.REPLACE
    assert document.scope == Scope()
    assert {o.key for o in document.root.children["organisations"]} == {("andere",), ("demo",)}
    assert summary.installation == "bron"
    assert document.meta["installation"] == "bron"
    assert list(transfer.work_dir.glob("*")) == []  # temporary copies are removed


async def test_applying_the_converted_document_replaces_the_configuration_and_reseals(
    engine: AsyncEngine, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _source(engine)
    package = _package(database_path(database_url), tmp_path)
    target_url = migrated_database(tmp_path, monkeypatch, "target")
    target = create_engine(target_url)
    try:
        await seed_group(target, slug="oud")  # configuration that must go
        transfer = SqliteConfigurationTransfer(
            database_path(target_url), TARGET_BOX, now=lambda: NOW
        )
        document, _ = await transfer.to_document(package, PASSPHRASE)
        documents = _documents(target)

        plan = await documents.plan_document(HOST, document)
        result = await documents.apply_document(HOST, document, plan.digest)

        assert result.applied, plan.refusals
        assert await CredentialStore(target, TARGET_BOX).get("webex_service_app") == {
            "token": "secret"
        }
        with closing(sqlite3.connect(database_path(target_url))) as db:
            slugs = [r[0] for r in db.execute("SELECT slug FROM organisations ORDER BY slug")]
            groups = [r[0] for r in db.execute("SELECT slug FROM groups ORDER BY slug")]
            assert db.execute("PRAGMA foreign_key_check").fetchone() is None
        assert slugs == ["andere", "demo"]
        assert "oud" not in groups  # replaced
    finally:
        await target.dispose()


async def test_a_replacing_package_refuses_what_the_actor_may_not_change(
    engine: AsyncEngine, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _source(engine)
    package = _package(database_path(database_url), tmp_path)
    target_url = migrated_database(tmp_path, monkeypatch, "target")
    target = create_engine(target_url)
    try:
        transfer = SqliteConfigurationTransfer(
            database_path(target_url), TARGET_BOX, now=lambda: NOW
        )
        document, _ = await transfer.to_document(package, PASSPHRASE)
        nobody = Actor("someone", principal("someone", "Someone", [Role.ORG_ADMIN], ["demo"]))

        plan = await _documents(target).plan_document(nobody, document)

        assert not plan.ok
        assert ErrorCode.NOT_PERMITTED in {r.code for r in plan.refusals}
    finally:
        await target.dispose()


async def test_another_schema_revision_and_rubbish_are_refused(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)
    package = _package(database_path(database_url), tmp_path)
    file = tmp_path / "other.sqlite"
    file.write_bytes(package)
    with closing(sqlite3.connect(file)) as db:
        db.execute("UPDATE alembic_version SET version_num = 'older'")
        db.commit()
    transfer = SqliteConfigurationTransfer(database_path(database_url), SOURCE_BOX, now=lambda: NOW)

    with pytest.raises(DomainError) as version:
        await transfer.to_document(file.read_bytes(), PASSPHRASE)
    with pytest.raises(DomainError) as rubbish:
        inspect_package(b"not a database", PASSPHRASE, tmp_path)
    with pytest.raises(DomainError) as plain:
        inspect_package(database_path(database_url).read_bytes(), PASSPHRASE, tmp_path)

    assert version.value.code is ErrorCode.CONFIGURATION_PACKAGE_VERSION
    assert rubbish.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID
    assert plain.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID  # no package table
    assert list(transfer.work_dir.glob("*")) == []


async def test_the_transfer_adapter_needs_a_settings_key(database_url: str) -> None:
    transfer = SqliteConfigurationTransfer(database_path(database_url), None, now=lambda: NOW)

    with pytest.raises(DomainError) as refused:
        await transfer.to_document(b"SQLite format 3\x00", PASSPHRASE)

    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY


async def test_a_package_with_a_trigger_or_another_column_set_is_refused(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)
    package = _package(database_path(database_url), tmp_path)
    transfer = SqliteConfigurationTransfer(database_path(database_url), SOURCE_BOX, now=lambda: NOW)
    crafted = {
        "trigger": "CREATE TRIGGER evil AFTER UPDATE ON organisations BEGIN SELECT 1; END",
        "view": "CREATE VIEW evil AS SELECT 1",
        "column": "ALTER TABLE organisations ADD COLUMN extra TEXT",
    }
    for name, statement in crafted.items():
        file = tmp_path / f"{name}.sqlite"
        file.write_bytes(package)
        with closing(sqlite3.connect(file)) as db:
            db.execute(statement)
            db.commit()

        with pytest.raises(DomainError) as refused:
            await transfer.to_document(file.read_bytes(), PASSPHRASE)

        assert refused.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID, name
    assert list(transfer.work_dir.glob("*")) == []
