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
import os
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import SecretStr

from app.adapters.crypto import SecretBox
from app.config import Settings
from app.domain.configuration_package import (
    CONFIGURATION_TABLES,
    COUNTED_TABLES,
    FORMAT,
    IN_USE_TABLES,
    PORTABLE_ENVIRONMENT,
    STATE_TABLES,
    VERSION_TABLE,
    PackageSummary,
)
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

_SEALED: tuple[tuple[str, str, str, str], ...] = (
    (
        "integration_credentials",
        "nonce",
        "ciphertext",
        "SELECT rowid, name, nonce, ciphertext FROM integration_credentials",
    ),
    (
        "channel_settings",
        "secret_nonce",
        "secret_ciphertext",
        "SELECT rowid, 'channel:' || organisation_id || ':' || channel, secret_nonce, "
        "secret_ciphertext FROM channel_settings WHERE secret_ciphertext IS NOT NULL",
    ),
    (
        "identity_providers",
        "secret_nonce",
        "secret_ciphertext",
        "SELECT rowid, 'idp:' || key || ':secret', secret_nonce, secret_ciphertext "
        "FROM identity_providers WHERE secret_ciphertext IS NOT NULL",
    ),
    (
        "log_destinations",
        "secret_nonce",
        "secret_ciphertext",
        "SELECT rowid, 'logdest:' || id || ':secret', secret_nonce, secret_ciphertext "
        "FROM log_destinations WHERE secret_ciphertext IS NOT NULL",
    ),
    (
        "identity_provider_keys",
        "key_nonce",
        "key_ciphertext",
        "SELECT k.rowid, 'idp:' || p.key || ':key:' || k.kid, k.key_nonce, k.key_ciphertext "
        "FROM identity_provider_keys k JOIN identity_providers p ON p.id = k.provider_id",
    ),
)
"""Every sealed column: table, nonce column, ciphertext column, and the query that yields
``rowid, record name, nonce, ciphertext``. The names are those of the writers (ADR 0008), so a
re-sealed value opens again on the other side under the same associated data."""


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


def _copy(source: Path, target: Path) -> None:
    """Copy one SQLite database into another with the online backup API."""
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


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


def _reseal(db: sqlite3.Connection, opener: SecretBox, sealer: SecretBox) -> int:
    """Open every sealed value with ``opener`` and seal it again with ``sealer``; count them.

    Raises:
        DomainError: ``configuration_passphrase_wrong`` when a value does not open (the
            verifier is checked first, so this means a damaged file; the same code keeps
            the two cases from being told apart by an attacker).
    """
    count = 0
    for table, nonce_column, cipher_column, query in _SEALED:
        rows = db.execute(query).fetchall()
        for rowid, name, nonce, ciphertext in rows:
            try:
                plaintext = opener.open(name, nonce, ciphertext)
            except InvalidTag as exc:
                raise DomainError(ErrorCode.CONFIGURATION_PASSPHRASE_WRONG, "passphrase") from exc
            new_nonce, new_ciphertext = sealer.seal(name, plaintext)
            db.execute(
                f"UPDATE {table} SET {nonce_column} = ?, {cipher_column} = ? WHERE rowid = ?",  # noqa: S608  # nosec B608 - names from the fixed list above
                (new_nonce, new_ciphertext, rowid),
            )
            count += 1
    return count


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
            _reseal(db, box, sealer)
            db.execute("COMMIT")
            db.execute("VACUUM")
        return copy.read_bytes()
    finally:
        _remove(copy)


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
        secrets = sum(len(db.execute(query).fetchall()) for *_, query in _SEALED)
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


def installation_in_use(database: Path) -> bool:
    """Return whether ``database`` has a row in any of the in-use tables (ADR 0089 point 5)."""
    with closing(_connect(database, read_only=True)) as db:
        return any(
            db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None  # noqa: S608  # nosec B608 - registry names
            for table in IN_USE_TABLES
        )


def import_package(
    database: Path, data: bytes, passphrase: str, box: SecretBox, work_dir: Path
) -> PackageSummary:
    """Replace every configuration table of ``database`` with the package's (point 5).

    The package is written to a temporary copy, its secrets are sealed again under ``box``,
    and the copy is attached to the database for the one transaction that empties and
    refills the configuration tables. ``settings_version`` moves past both the old and the
    package's value, so every process reloads the settings.

    Raises:
        DomainError: ``configuration_package_invalid``, ``configuration_passphrase_wrong``,
            ``configuration_package_version`` when the schema revisions or columns differ.
    """
    if not _is_sqlite(data):
        raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
    path = _temporary(work_dir, data)
    try:
        db, opener, summary = _open_package(path, passphrase)
        db.close()
        with closing(_connect(path)) as package:
            package.execute("BEGIN IMMEDIATE")
            _reseal(package, opener, box)
            package.execute("COMMIT")
        with closing(_connect(database)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            if _revision(db) != summary.revision:
                raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_VERSION, "package")
            db.execute("ATTACH DATABASE ? AS package", (str(path),))
            try:
                _replace_configuration(db)
            finally:
                db.execute("DETACH DATABASE package")
        return summary
    finally:
        _remove(path)


def _replace_configuration(db: sqlite3.Connection) -> None:
    """Empty and refill every configuration table from the attached ``package`` schema."""
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("PRAGMA defer_foreign_keys = ON")
        tables = sorted(CONFIGURATION_TABLES)
        plans: list[tuple[str, str]] = []
        for table in tables:
            columns = _columns(db, table)
            if set(columns) != set(_columns(db, table, "package")):
                raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_VERSION, "package")
            plans.append((table, ", ".join(columns)))
        previous = db.execute("SELECT version FROM settings_version WHERE id = 1").fetchone()
        for table in tables:
            db.execute(f"DELETE FROM main.{table}")  # noqa: S608  # nosec B608 - registry names
        for table, names in plans:
            db.execute(
                f"INSERT INTO main.{table} ({names}) SELECT {names} FROM package.{table}"  # noqa: S608  # nosec B608 - registry names
            )
        imported = db.execute("SELECT version FROM settings_version WHERE id = 1").fetchone()
        version = max(int(previous[0]) if previous else 0, int(imported[0]) if imported else 0) + 1
        db.execute("DELETE FROM settings_version")
        db.execute("INSERT INTO settings_version (id, version) VALUES (1, ?)", (version,))
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DomainError(ErrorCode.CONFIGURATION_PACKAGE_INVALID, "package")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def portable_environment(settings: Settings) -> dict[str, str]:
    """Return the portable environment values (point 4) as the process runs with them.

    Secrets are opened here, to be sealed into the package at once; unset values are left out.
    """
    portable: dict[str, str] = {}
    for key in PORTABLE_ENVIRONMENT:
        value = getattr(settings, key)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or value == "":
            continue
        portable[key.upper()] = str(value)
    return portable


class SqliteConfigurationTransfer:
    """The ``ConfigurationTransfer`` port on the installation's SQLite file.

    Args:
        database: The SQLite file.
        box: Opens and seals the installation's secrets; ``None`` without a settings key.
        installation: ``INSTALLATION_NAME``.
        app_version: The running release.
        environment: Returns the portable environment values at export time.
        now: The clock.
    """

    def __init__(
        self,
        database: Path,
        box: SecretBox | None,
        *,
        installation: str,
        app_version: str,
        environment: Callable[[], Mapping[str, str]],
        now: Callable[[], datetime],
    ) -> None:
        """Store the collaborators."""
        self._database = database
        self._box = box
        self._installation = installation
        self._app_version = app_version
        self._environment = environment
        self._now = now
        self._work_dir = database.parent / "transfer"

    @property
    def work_dir(self) -> Path:
        """Where temporary copies go: ``transfer`` next to the database, mode 700."""
        return self._work_dir

    def _require_box(self) -> SecretBox:
        """Return the box, or refuse: without a settings key no secret can be moved."""
        if self._box is None:
            raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key")
        return self._box

    async def export(self, passphrase: str) -> bytes:
        """Return a package sealed under ``passphrase`` (see ``export_package``)."""
        box = self._require_box()
        return await asyncio.to_thread(
            export_package,
            self._database,
            box,
            passphrase,
            created_at=self._now(),
            installation=self._installation,
            app_version=self._app_version,
            environment=dict(self._environment()),
            work_dir=self._work_dir,
        )

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        """Return the package's summary (see ``inspect_package``)."""
        return await asyncio.to_thread(inspect_package, package, passphrase, self._work_dir)

    async def import_(self, package: bytes, passphrase: str) -> PackageSummary:
        """Replace the configuration with the package's (see ``import_package``)."""
        box = self._require_box()
        return await asyncio.to_thread(
            import_package, self._database, package, passphrase, box, self._work_dir
        )

    async def in_use(self) -> bool:
        """Return whether the installation has bookings, slots or an active closure."""
        return await asyncio.to_thread(installation_in_use, self._database)
