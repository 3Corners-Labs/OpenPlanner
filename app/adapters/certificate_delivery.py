"""``CertificateDelivery`` on a directory the proxy reads: the ``portal-certs`` volume (ADR 0066).

Three files: ``fullchain.pem``, ``privkey.pem`` (mode 600) and, written last, ``serial`` with
the fingerprint. Each lands through a temporary name and a rename, so the proxy's watcher
never sees a half file: it reloads when ``serial`` changes, and by then both PEM files are in
place. Withdrawing deletes ``serial`` first, so the watcher goes back to ACME before the key
is gone. The key is opened here, from the sealed row, and written only to that one file. A
volume that cannot be written (missing, read-only, full) is logged by the exception's type and
leaves the proxy as it was; nothing here raises.
"""

import os
from pathlib import Path

import structlog

from app.adapters.db.uow import SqlTransaction, SqlUnitOfWork

log = structlog.get_logger()

CHAIN_FILE = "fullchain.pem"
KEY_FILE = "privkey.pem"
SERIAL_FILE = "serial"


class FileCertificateDelivery:
    """Writes the stored pair into ``directory`` for the proxy, or removes it.

    Args:
        uow: Opens the row that holds the pair (the key is opened only here).
        directory: The volume shared with the proxy (``TLS_PORTAL_CERTIFICATE_DIR``).
    """

    def __init__(self, uow: SqlUnitOfWork, directory: Path) -> None:
        """Store the collaborators; nothing is written yet."""
        self._uow = uow
        self._directory = directory

    async def publish(self, fingerprint: str) -> None:
        """Write the pair with ``fingerprint``; a pair already there with it is left alone."""
        serial = self._directory / SERIAL_FILE
        try:
            if serial.is_file() and serial.read_text().strip() == fingerprint:
                return
        except OSError as exc:
            log.warning("tls.delivery.failed", error=type(exc).__name__)
            return
        async with self._uow.read() as tx:
            sql_tx: SqlTransaction = tx
            stored = await sql_tx.get_own_certificate()
            key = await sql_tx.own_certificate_key()
        if stored is None or key is None or stored.fingerprint != fingerprint:
            log.warning("tls.delivery.nothing_to_publish")
            return
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            _write(self._directory / KEY_FILE, key, 0o600)
            _write(self._directory / CHAIN_FILE, stored.chain_pem, 0o644)
            _write(serial, fingerprint + "\n", 0o644)
        except OSError as exc:
            log.warning("tls.delivery.failed", error=type(exc).__name__)
            return
        log.info("tls.delivery.published", fingerprint=fingerprint)

    async def withdraw(self) -> None:
        """Remove the serial first, then the pair; nothing there is fine."""
        serial = self._directory / SERIAL_FILE
        try:
            if not serial.exists():
                return
            serial.unlink()
            for name in (KEY_FILE, CHAIN_FILE):
                (self._directory / name).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("tls.delivery.failed", error=type(exc).__name__)
            return
        log.info("tls.delivery.withdrawn")


def _write(target: Path, content: str, mode: int) -> None:
    """Write ``content`` to ``target`` through a temporary file with ``mode``, then rename."""
    temporary = target.with_name(target.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
    os.chmod(temporary, mode)
    os.replace(temporary, target)
