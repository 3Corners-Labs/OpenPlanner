"""Configuration packages on SQLite (ADR 0089): export, inspect and import.

A package is a copy of the database made with the online backup API, with every state table
emptied and vacuumed away, the sealed secrets opened with the installation's ``SecretBox`` and
sealed again under a key derived from the passphrase (scrypt), a verifier to tell a wrong
passphrase from a damaged file, and the portable environment values of ADR 0089 point 4. An
import does the reverse on a temporary copy and then replaces every configuration table in
one ``BEGIN IMMEDIATE`` transaction with foreign keys deferred and checked before the commit.

Everything here runs on the synchronous ``sqlite3`` module in a worker thread, as the backups
of ADR 0029 do; temporary files live next to the database (mode 600) and are removed again.
Raw SQL is allowed in this package only (CLAUDE.md). No value is ever logged.
"""

import asyncio
import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.adapters.crypto import SecretBox
from app.adapters.db.engine import create_engine
from app.adapters.db.sealed import SEALED, reseal
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.configuration_document import Document, Mode, Scope
from app.domain.configuration_package import (
    COUNTED_TABLES,
    FORMAT,
    VERSION_TABLE,
    PackageSummary,
)
from app.domain.configuration_v1 import V1
from app.domain.errors import DomainError, ErrorCode

SCRYPT_N = 2**16
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
"""The passphrase key derivation (ADR 0089 point 3): crypto parameters, fixed."""

META_TABLE = "configuration_package"
ENVIRONMENT_TABLE = "configuration_environment"
VERIFIER_NAME = "package:verifier"
VERIFIER_TEXT = b"openplanner configuration package"
ENVIRONMENT_PREFIX = "env:"
SQLITE_HEADER = b"SQLite format 3\x00"
BUSY_TIMEOUT_SECONDS = 5.0


def package_box(passphrase: str, salt: bytes) -> SecretBox:
    """Return the box of a package: its key is scrypt of the passphrase and the salt."""
    key = Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        passphrase.encode()
    )
    return SecretBox(key)


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open ``path`` with explicit transaction control and a busy timeout."""
    if read_only:
        uri = f"file:{path}?mode=ro"
        return sqlite3.connect(uri, uri=True, isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)
    return sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)


def _remove(path: Path) -> None:
    """Remove a temporary copy with the journal files SQLite may have left next to it."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _temporary(work_dir: Path, data: bytes | None = None) -> Path:
    """Return a new file (mode 600) in ``work_dir``, holding ``data`` when given."""
    work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=work_dir, suffix=".sqlite")
    with os.fdopen(fd, "wb") as handle:
        if data:
            handle.write(data)
    return Path(name)


def _reseal_or_refuse(db: sqlite3.Connection, opener: SecretBox, sealer: SecretBox) -> None:
    """Re-seal every value; a value that does not open is a wrong passphrase or damaged file.

    Raises:
        DomainError: ``configuration_passphrase_wrong`` (the verifier is checked first, so this
            means a damaged file; the same code keeps the two cases from being told apart).
    """
    try:
        reseal(db, opener, sealer)
    except InvalidTag as exc:
        raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_WRONG, "passphrase") from exc


def _revision(db: sqlite3.Connection, schema: str = "main") -> str:
    """Return the schema revision stored by Alembic, or empty when the table is missing."""
    try:
        row = db.execute(f"SELECT version_num FROM {schema}.{VERSION_TABLE}").fetchone()  # noqa: S608  # nosec B608 - fixed names
    except sqlite3.DatabaseError:
        return ""
    return str(row[0]) if row else ""


def _columns(db: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    """Return the column names of ``schema.table``."""
    return [str(r[1]) for r in db.execute(f"PRAGMA {schema}.table_info({table})").fetchall()]


def _open_package(
    path: Path, passphrase: str
) -> tuple[sqlite3.Connection, SecretBox, PackageSummary]:
    """Open a package read-only, check its format and passphrase, and summarise it.

    The caller closes the connection.

    Raises:
        DomainError: ``configuration_package_invalid``, ``configuration_passphrase_wrong``.
    """
    db = _connect(path, read_only=True)
    try:
        meta = db.execute(
            f"SELECT format, created_at, installation, app_version, kdf_salt, verifier_nonce, "  # noqa: S608  # nosec B608 - fixed name
            f"verifier_ciphertext FROM {META_TABLE} WHERE id = 1"
        ).fetchone()
        if meta is None or int(meta[0]) != FORMAT:
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
        opener = package_box(passphrase, meta[4])
        try:
            opener.open(VERIFIER_NAME, meta[5], meta[6])
        except InvalidTag as exc:
            raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_WRONG, "passphrase") from exc
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608  # nosec B608 - registry names
            for table in COUNTED_TABLES
        }
        secrets = sum(len(db.execute(query).fetchall()) for *_, query in SEALED)
        environment = tuple(
            str(r[0])
            for r in db.execute(f"SELECT key FROM {ENVIRONMENT_TABLE} ORDER BY key")  # noqa: S608  # nosec B608 - fixed name
        )
        summary = PackageSummary(
            format=int(meta[0]),
            created_at=datetime.fromisoformat(meta[1]),
            installation=str(meta[2]),
            app_version=str(meta[3]),
            revision=_revision(db),
            counts=counts,
            secrets=secrets,
            environment=environment,
        )
    except (sqlite3.DatabaseError, ValueError, TypeError) as exc:
        db.close()
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package") from exc
    except DomainError:
        db.close()
        raise
    return db, opener, summary


def _is_sqlite(data: bytes) -> bool:
    """Return whether ``data`` starts like a SQLite file (cheap, before writing it out)."""
    return data[: len(SQLITE_HEADER)] == SQLITE_HEADER


def inspect_package(data: bytes, passphrase: str, work_dir: Path) -> PackageSummary:
    """Return what the package ``data`` holds, once ``passphrase`` opens it.

    Raises:
        DomainError: ``configuration_package_invalid``, ``configuration_passphrase_wrong``.
    """
    if not _is_sqlite(data):
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
    path = _temporary(work_dir, data)
    try:
        db, _, summary = _open_package(path, passphrase)
        db.close()
        return summary
    finally:
        _remove(path)


def environment_values(data: bytes, passphrase: str, work_dir: Path) -> dict[str, str]:
    """Return the portable environment values of the package, by upper-case name.

    Raises:
        DomainError: ``configuration_package_invalid``, ``configuration_passphrase_wrong``.
    """
    if not _is_sqlite(data):
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
    path = _temporary(work_dir, data)
    try:
        db, opener, _ = _open_package(path, passphrase)
        with closing(db):
            rows = db.execute(
                f"SELECT key, nonce, ciphertext FROM {ENVIRONMENT_TABLE} ORDER BY key"  # noqa: S608  # nosec B608 - fixed name
            ).fetchall()
            return {
                str(key): opener.open(ENVIRONMENT_PREFIX + str(key), nonce, ciphertext).decode()
                for key, nonce, ciphertext in rows
            }
    finally:
        _remove(path)


def _prepare(
    data: bytes, passphrase: str, box: SecretBox, live: Path, work_dir: Path
) -> tuple[Path, PackageSummary]:
    """Write the package to a temporary copy, open it, and seal its secrets under ``box``.

    Returns the copy (the caller removes it) and the package's summary.

    Raises:
        DomainError: ``configuration_package_invalid``, ``configuration_passphrase_wrong``,
            ``configuration_package_version`` when its schema revision is not the running one.
    """
    if not _is_sqlite(data):
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
    path = _temporary(work_dir, data)
    try:
        db, opener, summary = _open_package(path, passphrase)
        db.close()
        with closing(_connect(path)) as package:
            package.execute("BEGIN IMMEDIATE")
            _reseal_or_refuse(package, opener, box)
            package.execute("COMMIT")
        with closing(_connect(live, read_only=True)) as running:
            if _revision(running) != summary.revision:
                raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_VERSION, "package")
    except BaseException:
        _remove(path)
        raise
    return path, summary


class SqliteConfigurationTransfer:
    """The ``ConfigurationTransfer`` port on SQLite packages: read, never written (ADR 0099).

    A package is read for one release after the JSON document replaced it: its secrets are
    opened and sealed under the installation's key on a temporary copy, the copy is read with
    the same readers as the live database, and what they return is a ``replace`` document in
    the platform scope.

    Args:
        database: The installation's SQLite file (for the schema revision it runs).
        box: Opens and seals the installation's secrets; ``None`` without a settings key.
        now: The clock.
    """

    def __init__(
        self, database: Path, box: SecretBox | None, *, now: Callable[[], datetime]
    ) -> None:
        """Store the collaborators."""
        self._database = database
        self._box = box
        self._now = now
        self._work_dir = database.parent / "transfer"

    @property
    def work_dir(self) -> Path:
        """Where temporary copies go: ``transfer`` next to the database, mode 700."""
        return self._work_dir

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        """Return the package's summary (see ``inspect_package``)."""
        return await asyncio.to_thread(inspect_package, package, passphrase, self._work_dir)

    async def to_document(self, package: bytes, passphrase: str) -> tuple[Document, PackageSummary]:
        """Return the package as a ``replace`` document of the platform, and its summary.

        Raises:
            DomainError: The codes of ``inspect``; ``configuration_package_version`` when the
                schema revisions differ; ``integration_not_ready`` without a settings key.
        """
        if self._box is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key")
        path, summary = await asyncio.to_thread(
            _prepare, package, passphrase, self._box, self._database, self._work_dir
        )
        try:
            engine = create_engine(f"sqlite+aiosqlite:///{path}")
            try:
                async with SqlUnitOfWork(engine, self._box).read() as tx:
                    snapshot = await tx.read_configuration(self._now(), frozenset())
            finally:
                await engine.dispose()
        finally:
            await asyncio.to_thread(_remove, path)
        document = Document(
            format=V1.name,
            mode=Mode.REPLACE,
            scope=Scope(),
            root=snapshot.root,
            digest=hashlib.sha256(package).hexdigest(),
            schema=V1,
            meta={
                "installation": summary.installation,
                "exported_at": summary.created_at.isoformat(),
                "app_version": summary.app_version,
            },
        )
        return document, summary
