"""A configuration package in the SQLite format of ADR 0089, made for the tests.

The portal, the API and the host no longer export this format (ADR 0099 step 4); they still
read it for one release, converted to a document. The tests need packages to read, so the
export that made them lives here, as a helper, and writes exactly what the old export did.
"""

import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.crypto import SecretBox
from app.adapters.db.configuration_package import (
    ENVIRONMENT_PREFIX,
    ENVIRONMENT_TABLE,
    META_TABLE,
    SALT_BYTES,
    VERIFIER_NAME,
    VERIFIER_TEXT,
    _connect,
    _remove,
    _reseal_or_refuse,
    _temporary,
    package_box,
)
from app.domain.configuration_package import FORMAT, STATE_TABLES


def _copy(source: Path, target: Path) -> None:
    """Copy one SQLite database into another with the online backup API."""
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def export_package(
    database: Path,
    box: SecretBox,
    passphrase: str,
    *,
    created_at: datetime,
    installation: str,
    app_version: str,
    environment: Mapping[str, str],
    work_dir: Path,
) -> bytes:
    """Return a package of ``database`` sealed under ``passphrase`` (ADR 0089 points 1–4).

    Args:
        database: The installation's SQLite file.
        box: Opens the installation's sealed values.
        passphrase: Seals the package.
        created_at: The export time, stored in the package.
        installation: ``INSTALLATION_NAME``, stored in the package.
        app_version: The running release, stored in the package.
        environment: The portable environment values, upper-case names.
        work_dir: Where the temporary copy is written (mode 600) and removed again.
    """
    salt = os.urandom(SALT_BYTES)
    sealer = package_box(passphrase, salt)
    copy = _temporary(work_dir)
    try:
        _copy(database, copy)
        with closing(_connect(copy)) as db:
            # One self-contained file: the copy inherits the database's WAL mode.
            db.execute("PRAGMA journal_mode = DELETE")
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("BEGIN IMMEDIATE")
            for table in sorted(STATE_TABLES):
                db.execute(f"DELETE FROM {table}")  # noqa: S608  # nosec B608 - names from the registry
            db.execute(
                f"CREATE TABLE {META_TABLE} (id INTEGER PRIMARY KEY CHECK (id = 1), "
                "format INTEGER NOT NULL, created_at TEXT NOT NULL, installation TEXT NOT NULL, "
                "app_version TEXT NOT NULL, kdf_salt BLOB NOT NULL, verifier_nonce BLOB NOT NULL, "
                "verifier_ciphertext BLOB NOT NULL)"
            )
            db.execute(
                f"CREATE TABLE {ENVIRONMENT_TABLE} (key TEXT PRIMARY KEY, nonce BLOB NOT NULL, "
                "ciphertext BLOB NOT NULL)"
            )
            nonce, ciphertext = sealer.seal(VERIFIER_NAME, VERIFIER_TEXT)
            db.execute(
                f"INSERT INTO {META_TABLE} VALUES (1, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608  # nosec B608 - fixed name
                (
                    FORMAT,
                    created_at.astimezone(UTC).isoformat(),
                    installation,
                    app_version,
                    salt,
                    nonce,
                    ciphertext,
                ),
            )
            for key, value in environment.items():
                nonce, ciphertext = sealer.seal(ENVIRONMENT_PREFIX + key, value.encode())
                db.execute(
                    f"INSERT INTO {ENVIRONMENT_TABLE} VALUES (?, ?, ?)",  # noqa: S608  # nosec B608 - fixed name
                    (key, nonce, ciphertext),
                )
            _reseal_or_refuse(db, box, sealer)
            db.execute("COMMIT")
            db.execute("VACUUM")
        return copy.read_bytes()
    finally:
        _remove(copy)
