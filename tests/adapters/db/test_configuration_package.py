"""Configuration packages on SQLite (ADR 0089): export, inspect, import and the registry."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.configuration_package import (
    SqliteConfigurationTransfer,
    environment_values,
    export_package,
    import_package,
    inspect_package,
    installation_in_use,
)
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.engine import create_engine
from app.adapters.db.models import Base, SlotRow, StaffRow
from app.adapters.db.seed import seed_demo
from app.domain.configuration_package import (
    CONFIGURATION_TABLES,
    STATE_TABLES,
    VERSION_TABLE,
)
from app.domain.errors import DomainError, ErrorCode
from tests.adapters.db.conftest import seed_group
from tests.support.database import migrated_database

SOURCE_BOX = SecretBox(b"s" * 32)
TARGET_BOX = SecretBox(b"t" * 32)
PASSPHRASE = "correct horse battery staple"  # noqa: S105 - a test passphrase
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
ENVIRONMENT = {"OIDC_DISCOVERY_URL": "https://idp.example/.well-known/openid-configuration"}


def _path(url: str) -> Path:
    return Path(url.removeprefix("sqlite+aiosqlite:///"))


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


def _export(database: Path, tmp_path: Path, **overrides: object) -> bytes:
    options: dict[str, object] = {
        "created_at": NOW,
        "installation": "bron",
        "app_version": "1.2.3",
        "environment": ENVIRONMENT,
        "work_dir": tmp_path / "work",
    }
    options.update(overrides)
    return export_package(database, SOURCE_BOX, PASSPHRASE, **options)  # type: ignore[arg-type]


def test_every_table_of_the_model_is_configuration_or_state() -> None:
    tables = set(Base.metadata.tables)

    assert tables == CONFIGURATION_TABLES | STATE_TABLES, (
        "classify every table in app/domain/configuration_package.py"
    )
    assert not CONFIGURATION_TABLES & STATE_TABLES
    assert VERSION_TABLE not in tables


def test_every_sealed_column_of_a_configuration_table_is_resealed() -> None:
    from app.adapters.db.configuration_package import _SEALED

    sealed = {
        (table.name, column.name)
        for table in Base.metadata.sorted_tables
        if table.name in CONFIGURATION_TABLES
        for column in table.columns
        if column.name.endswith("ciphertext")
    }

    assert sealed == {(table, cipher) for table, _, cipher, _ in _SEALED}, (
        "a sealed column needs an entry in _SEALED, or its secret is unreadable after an import"
    )


async def test_export_keeps_configuration_and_drops_state(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)

    package = _export(_path(database_url), tmp_path)

    assert package.startswith(b"SQLite format 3\x00")
    assert b"Sanne" not in package  # staff is state, and vacuumed away
    assert list((tmp_path / "work").glob("*.sqlite")) == []  # the copy is removed again
    summary = inspect_package(package, PASSPHRASE, tmp_path / "work")
    assert summary.counts == {
        "organisations": 2,
        "groups": 2,
        "identity_providers": 0,
        "api_clients": 0,
    }
    assert summary.secrets == 1
    assert summary.installation == "bron"
    assert summary.app_version == "1.2.3"
    assert summary.environment == ("OIDC_DISCOVERY_URL",)
    assert summary.created_at == NOW
    assert summary.revision  # the alembic revision travels with the package


async def test_secrets_are_sealed_under_the_passphrase_not_the_installation_key(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)

    package = _export(_path(database_url), tmp_path)

    file = tmp_path / "p.sqlite"
    file.write_bytes(package)
    with closing(sqlite3.connect(file)) as db:
        nonce, ciphertext = db.execute(
            "SELECT nonce, ciphertext FROM integration_credentials"
        ).fetchone()
    with pytest.raises(InvalidTag):
        SOURCE_BOX.open("webex_service_app", nonce, ciphertext)
    with pytest.raises(DomainError) as refused:
        inspect_package(package, "not the passphrase", tmp_path / "work")
    assert refused.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_WRONG
    assert environment_values(package, PASSPHRASE, tmp_path / "work") == ENVIRONMENT


async def test_import_replaces_the_configuration_and_reseals_the_secrets(
    engine: AsyncEngine, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _source(engine)
    package = _export(_path(database_url), tmp_path)
    target_url = migrated_database(tmp_path, monkeypatch, "target")
    target = create_engine(target_url)
    try:
        await seed_group(target, slug="oud")  # configuration that must go
        with closing(sqlite3.connect(_path(target_url))) as db:
            db.execute("INSERT INTO settings_version (id, version) VALUES (1, 7)")
            db.commit()

        summary = import_package(
            _path(target_url), package, PASSPHRASE, TARGET_BOX, tmp_path / "work"
        )

        assert summary.counts["organisations"] == 2
        store = CredentialStore(target, TARGET_BOX)
        assert await store.get("webex_service_app") == {"token": "secret"}
        with closing(sqlite3.connect(_path(target_url))) as db:
            slugs = [r[0] for r in db.execute("SELECT slug FROM organisations ORDER BY slug")]
            version = db.execute("SELECT version FROM settings_version").fetchone()[0]
            staff = db.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
            assert db.execute("PRAGMA foreign_key_check").fetchone() is None
        assert slugs == ["andere", "demo"]
        assert version == 8  # past the old value, so every process reloads
        assert staff == 0  # the target's state is not touched (and the source's not copied)
    finally:
        await target.dispose()


async def test_import_refuses_another_schema_revision_and_rubbish(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)
    package = _export(_path(database_url), tmp_path)
    file = tmp_path / "other.sqlite"
    file.write_bytes(package)
    with closing(sqlite3.connect(file)) as db:
        db.execute("UPDATE alembic_version SET version_num = 'older'")
        db.commit()

    with pytest.raises(DomainError) as version:
        import_package(_path(database_url), file.read_bytes(), PASSPHRASE, SOURCE_BOX, tmp_path)
    with pytest.raises(DomainError) as rubbish:
        inspect_package(b"not a database", PASSPHRASE, tmp_path)
    with pytest.raises(DomainError) as plain:
        inspect_package(_path(database_url).read_bytes(), PASSPHRASE, tmp_path)

    assert version.value.code is ErrorCode.CONFIGURATION_PACKAGE_VERSION
    assert rubbish.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID
    assert plain.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID  # no package table


async def test_an_installation_with_a_slot_is_in_use(
    engine: AsyncEngine, database_url: str
) -> None:
    group = await seed_group(engine)
    assert not installation_in_use(_path(database_url))

    async with AsyncSession(engine) as session, session.begin():
        session.add(
            SlotRow(group_id=group, start=NOW, end=NOW, capacity_snapshot=1, kind="scheduled")
        )

    assert installation_in_use(_path(database_url))


async def test_the_transfer_adapter_needs_a_settings_key(database_url: str) -> None:
    transfer = SqliteConfigurationTransfer(
        _path(database_url),
        None,
        installation="x",
        app_version="",
        environment=dict,
        now=lambda: NOW,
    )

    with pytest.raises(DomainError) as refused:
        await transfer.export(PASSPHRASE)

    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY
    assert not await transfer.in_use()


async def test_the_transfer_adapter_round_trips(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await _source(engine)
    transfer = SqliteConfigurationTransfer(
        _path(database_url),
        SOURCE_BOX,
        installation="bron",
        app_version="1.0.0",
        environment=lambda: ENVIRONMENT,
        now=lambda: NOW,
    )

    package = await transfer.export(PASSPHRASE)
    summary = await transfer.import_(package, PASSPHRASE)

    assert summary.counts["groups"] == 2
    assert (await transfer.inspect(package, PASSPHRASE)).environment == ("OIDC_DISCOVERY_URL",)
    assert transfer.work_dir == _path(database_url).parent / "transfer"
    assert list(transfer.work_dir.glob("*")) == []  # temporary copies are removed
