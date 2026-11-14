"""A package never holds a secret that is on its way out (ADR 0124 point 8).

An organisation's previous webhook secret and what its last registration came to live in its sealed
contact-center record. The application no longer exports a package (ADR 0099 step 4), but the
export that made one (``tests/support/legacy_package``) and the reading of one share the one place
where a package's copy of a sealed record is made, ``_reseal_or_refuse``. The tests export from a
database with a rotated connection and read what the package holds.
"""

import json
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.db.configuration_package import (
    SqliteConfigurationTransfer,
    _open_package,
)
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.record_names import contact_center_credential_name
from app.adapters.db.sealed import without_rotation_state
from app.domain.contact_center import ConnectionChange, ContactCenterKind, Registration
from tests.support.configuration_package import NOW, PASSPHRASE, SOURCE_BOX, database_path
from tests.support.legacy_package import export_package

WXCC = ContactCenterKind.WEBEX_CC
NAME = contact_center_credential_name(1)


async def _rotated(engine: AsyncEngine) -> tuple[SealedConnections, str, str]:
    """A connection that was rotated once and whose registration was left in a gap."""
    connections = SealedConnections(CredentialStore(engine, SOURCE_BOX))
    await connections.save(
        1, ConnectionChange(WXCC, "https://api.wxcc-eu1.cisco.com", "client", "cs", "rt"), NOW
    )
    old = await connections.webhook_secret(1, WXCC)
    await connections.rotate_webhook_secret(1, NOW + timedelta(minutes=5))
    await connections.set_registration(1, Registration.GONE, NOW + timedelta(minutes=5))
    new = await connections.webhook_secret(1, WXCC)
    assert old is not None
    assert new is not None
    return connections, old, new


def _exported(database_url: str, tmp_path: Path) -> bytes:
    return export_package(
        database_path(database_url),
        SOURCE_BOX,
        PASSPHRASE,
        created_at=NOW,
        installation="bron",
        app_version="1.2.3",
        environment={},
        work_dir=tmp_path / "work",
    )


def _record_in(package: bytes, tmp_path: Path) -> dict[str, object]:
    """Open the package with its passphrase and return the connection's record."""
    path = tmp_path / "package.sqlite"
    path.write_bytes(package)
    db, opener, _ = _open_package(path, PASSPHRASE)
    with closing(db):
        nonce, ciphertext = db.execute(
            "SELECT nonce, ciphertext FROM integration_credentials WHERE name = ?", (NAME,)
        ).fetchone()
    record: dict[str, object] = json.loads(opener.open(NAME, nonce, ciphertext))
    return record


async def test_an_exported_package_holds_the_current_secret_and_not_what_a_rotation_left(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    _, old, new = await _rotated(engine)

    package = _exported(database_url, tmp_path)
    record = _record_in(package, tmp_path)

    assert record["webhook_secret"] == new  # the connection itself is configuration
    assert record["client_secret"] == "cs"
    assert "previous_webhook_secret" not in record
    assert "registration" not in record
    assert "webhook_secret_at" not in record  # the overlap was counted from it
    assert old.encode() not in package  # sealed, so only a look inside tells; checked above
    assert old not in json.dumps(record)


async def test_a_connection_that_was_never_rotated_keeps_the_time_its_secret_was_made(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    connections = SealedConnections(CredentialStore(engine, SOURCE_BOX))
    await connections.save(
        1, ConnectionChange(WXCC, "https://api.wxcc-eu1.cisco.com", "client", "cs", "rt"), NOW
    )

    record = _record_in(_exported(database_url, tmp_path), tmp_path)

    assert record["webhook_secret_at"] == NOW.isoformat()


async def test_the_live_database_keeps_every_field_the_package_leaves_out(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    connections, old, _ = await _rotated(engine)

    _exported(database_url, tmp_path)

    secrets = await connections.webhook_secrets(1, WXCC)
    shown = await connections.get(1)
    assert secrets is not None
    assert secrets.previous == old
    assert shown is not None
    assert shown.registration is Registration.GONE


async def test_reading_such_a_package_leaves_the_installation_without_a_previous_secret(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    """The import reads a package as a document: no connection, so no previous secret, arrives."""
    _, old, new = await _rotated(engine)
    package = _exported(database_url, tmp_path)
    transfer = SqliteConfigurationTransfer(database_path(database_url), SOURCE_BOX, now=lambda: NOW)

    # The package reads on a temporary copy; the document it makes holds neither secret.
    document, _ = await transfer.to_document(package, PASSPHRASE)

    text = repr(document)
    assert old not in text
    assert new not in text


def test_only_a_contact_center_record_is_scrubbed_and_anything_else_is_left_as_it_was() -> None:
    other = json.dumps({"previous_webhook_secret": "x", "registration": "gone"}).encode()

    assert without_rotation_state("channel:1:sms", other) == other
    assert without_rotation_state(NAME, b"not json") == b"not json"
    assert without_rotation_state(NAME, b"[1, 2]") == b"[1, 2]"
    scrubbed = json.loads(without_rotation_state(NAME, other))
    assert scrubbed == {}
