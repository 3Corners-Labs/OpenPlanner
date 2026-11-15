"""Every sealed column of the database, and re-sealing them (ADR 0089, ADR 0094).

One home for the operation that opens each sealed value and seals it again under another
key: the configuration package seals under a passphrase key (export) and under the
installation's key (import); ``rekey`` seals under the current ``SETTINGS_ENCRYPTION_KEY``
after a rotation. Raw SQL is allowed in this package only (CLAUDE.md). No value is logged.
"""

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.adapters.crypto import SecretBox

BUSY_TIMEOUT_SECONDS = 30.0

SEALED: tuple[tuple[str, str, str, str], ...] = (
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


ROTATION_FIELDS = ("previous_webhook_secret", "registration")
"""The fields of an organisation's sealed contact-center record that say what a rotation left
(ADR 0124 point 8): the secret on its way out and what the last registration came to."""
CONTACT_CENTER_PREFIX = "contact-center:"
"""The record name of an organisation's contact-center connection (``record_names``)."""


def without_rotation_state(name: str, plaintext: bytes) -> bytes:
    """Return a sealed record's plaintext without what a rotation left, for a package's copy.

    A package never holds a secret that is on its way out: from an organisation's contact-center
    record go the previous webhook secret and the registration outcome, and, when there was a
    previous secret, the time of the rotation too (the overlap was counted from it; the current
    secret's own time is kept when nothing was rotated). Any other record, and one that is not
    a JSON object, is returned as it was.
    """
    if not name.startswith(CONTACT_CENTER_PREFIX):
        return plaintext
    try:
        record = json.loads(plaintext)
    except ValueError:
        return plaintext
    if not isinstance(record, dict):
        return plaintext
    if record.get("previous_webhook_secret"):
        record.pop("webhook_secret_at", None)
    for field_name in ROTATION_FIELDS:
        record.pop(field_name, None)
    return json.dumps(record).encode()


@dataclass(frozen=True)
class RekeyCounts:
    """What a re-sealing run did, counts only.

    Attributes:
        resealed: Values sealed again under the current key.
        current: Values that already were under the current key and were left alone.
    """

    resealed: int
    current: int


def reseal(
    db: sqlite3.Connection,
    opener: SecretBox,
    sealer: SecretBox,
    *,
    only_stale: bool = False,
    scrub: Callable[[str, bytes], bytes] | None = None,
) -> RekeyCounts:
    """Open every sealed value with ``opener`` and seal it again with ``sealer``; count them.

    With ``only_stale`` a value that already carries ``sealer``'s key id is left alone, which
    makes a second run a no-op (``rekey``). ``scrub`` changes each opened value before it is
    sealed again (a package's copy leaves out what a rotation left, ``without_rotation_state``);
    ``rekey`` never passes one, so the live database keeps every field. The caller owns the
    transaction.

    Raises:
        InvalidTag: When a value does not open with ``opener``.
    """
    resealed = current = 0
    for table, nonce_column, cipher_column, query in SEALED:
        for rowid, name, nonce, ciphertext in db.execute(query).fetchall():
            if only_stale and sealer.is_current(nonce):
                current += 1
                continue
            plaintext = opener.open(name, nonce, ciphertext)
            if scrub is not None:
                plaintext = scrub(name, plaintext)
            new_nonce, new_ciphertext = sealer.seal(name, plaintext)
            db.execute(
                f"UPDATE {table} SET {nonce_column} = ?, {cipher_column} = ? WHERE rowid = ?",  # noqa: S608  # nosec B608 - names from the fixed list above
                (new_nonce, new_ciphertext, rowid),
            )
            resealed += 1
    return RekeyCounts(resealed, current)


def count_stale(db: sqlite3.Connection, box: SecretBox) -> int:
    """Return how many sealed values are not under ``box``'s current key."""
    return sum(
        1
        for *_, query in SEALED
        for _rowid, _name, nonce, _ciphertext in db.execute(query).fetchall()
        if not box.is_current(nonce)
    )


def rekey_database(database: Path, box: SecretBox) -> RekeyCounts:
    """Seal every value of ``database`` again under ``box``'s current key (ADR 0094 point 3).

    One ``BEGIN IMMEDIATE`` transaction, so a secret that the running application writes
    meanwhile is either in it or sealed under the current key already. Idempotent: values
    that carry the current key id are left alone.

    Raises:
        InvalidTag: When a value opens under no configured key (the previous key is missing);
            nothing is changed then.
    """
    with closing(
        sqlite3.connect(database, isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)
    ) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            counts = reseal(db, box, box, only_stale=True)
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
    return counts


def stale_count(database: Path, box: SecretBox) -> int:
    """Return how many sealed values of ``database`` are not under ``box``'s current key."""
    uri = f"file:{database}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS)) as db:
        return count_stale(db, box)
