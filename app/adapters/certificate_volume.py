"""``CertificateVolume`` on a directory the proxy reads: the ``portal-certs`` volume (ADR 0068).

Files, all written through a temporary name and a rename so the proxy's watcher never sees a
half file:

- ``privkey.pem`` (mode 600): the key the proxy serves with. Made here for a signing request
  or imported here; it never goes anywhere else, so a database backup never carries it.
- ``pending.key`` (600) and ``pending.csr``: a new key waiting for its certificate, kept apart
  so a certificate in use is never disturbed.
- ``fullchain.pem`` and, written last, ``serial`` with the fingerprint: the served pair. The
  watcher reloads when ``serial`` changes, and by then the chain is in place; withdrawing
  deletes ``serial`` first.

Reading and publishing never raise (a volume that cannot be read or written is logged by the
exception's type and the proxy stays as it was); making or importing a key raises
``tls_volume_unwritable``, because the superuser must hear that.
"""

import os
from pathlib import Path

import structlog

from app.adapters.certificate_pem import load_key, new_key_and_request, public_key
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import HostKeys

log = structlog.get_logger()

KEY_FILE = "privkey.pem"
CHAIN_FILE = "fullchain.pem"
SERIAL_FILE = "serial"
PENDING_KEY_FILE = "pending.key"
PENDING_CSR_FILE = "pending.csr"

_PRIVATE = 0o600
_PUBLIC = 0o644


class FileCertificateVolume:
    """The keys, the request and the served pair in ``directory``.

    Args:
        directory: The volume shared with the proxy (``TLS_PORTAL_CERTIFICATE_DIR``).
    """

    def __init__(self, directory: Path) -> None:
        """Store the directory; nothing is read or written yet."""
        self._directory = directory

    def keys(self) -> HostKeys:
        """Return the public keys of the served and the pending key, and the pending request."""
        try:
            return HostKeys(
                _public_key_of(self._directory / KEY_FILE),
                _public_key_of(self._directory / PENDING_KEY_FILE),
                _read_text(self._directory / PENDING_CSR_FILE),
            )
        except OSError as exc:
            log.warning("tls.volume.unreadable", error=type(exc).__name__)
            return HostKeys()

    def generate_pending(self, host: str) -> str:
        """Make a new key and request for ``host``, replacing an earlier pending one."""
        key, request = new_key_and_request(host)
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            _write(self._directory / PENDING_KEY_FILE, key, _PRIVATE)
            _write(self._directory / PENDING_CSR_FILE, request, _PUBLIC)
        except OSError as exc:
            raise _unwritable(exc) from exc
        log.info("tls.volume.request_made")
        return request

    def promote_pending(self) -> None:
        """Rename the pending key to the served one and drop the request."""
        try:
            os.replace(self._directory / PENDING_KEY_FILE, self._directory / KEY_FILE)
            (self._directory / PENDING_CSR_FILE).unlink(missing_ok=True)
        except OSError as exc:
            raise _unwritable(exc) from exc
        log.info("tls.volume.key_promoted")

    def import_key(self, key_pem: str) -> None:
        """Write an imported key as the served one and drop any pending request."""
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            _write(self._directory / KEY_FILE, key_pem, _PRIVATE)
            for name in (PENDING_KEY_FILE, PENDING_CSR_FILE):
                (self._directory / name).unlink(missing_ok=True)
        except OSError as exc:
            raise _unwritable(exc) from exc
        log.info("tls.volume.key_imported")

    def publish(self, chain_pem: str, fingerprint: str) -> None:
        """Write the chain and the serial; a pair already there with this serial is left alone."""
        serial = self._directory / SERIAL_FILE
        try:
            if serial.is_file() and serial.read_text().strip() == fingerprint:
                return
            if not (self._directory / KEY_FILE).is_file():
                log.warning("tls.volume.no_key_to_serve_with")
                return
            _write(self._directory / CHAIN_FILE, chain_pem, _PUBLIC)
            _write(serial, fingerprint + "\n", _PUBLIC)
        except OSError as exc:
            log.warning("tls.volume.publish_failed", error=type(exc).__name__)
            return
        log.info("tls.volume.published", fingerprint=fingerprint)

    def withdraw(self) -> None:
        """Remove the serial first, then the chain; the key stays; nothing there is fine."""
        serial = self._directory / SERIAL_FILE
        try:
            if not serial.exists():
                return
            serial.unlink()
            (self._directory / CHAIN_FILE).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("tls.volume.withdraw_failed", error=type(exc).__name__)
            return
        log.info("tls.volume.withdrawn")


def _public_key_of(path: Path) -> bytes | None:
    """Return the public key of the PEM key at ``path``; ``None`` without a readable key."""
    if not path.is_file():
        return None
    try:
        return public_key(load_key(path.read_bytes()))
    except DomainError:
        log.warning("tls.volume.key_unreadable", file=path.name)
        return None


def _read_text(path: Path) -> str | None:
    """Return the text at ``path``, or ``None`` when there is no file."""
    return path.read_text() if path.is_file() else None


def _write(target: Path, content: str, mode: int) -> None:
    """Write ``content`` to ``target`` through a temporary file with ``mode``, then rename."""
    temporary = target.with_name(target.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
    os.chmod(temporary, mode)
    os.replace(temporary, target)


def _unwritable(exc: OSError) -> DomainError:
    """Turn a failed write into the refusal the superuser sees; the type is logged."""
    log.warning("tls.volume.unwritable", error=type(exc).__name__)
    return DomainError(ErrorCode.TLS_VOLUME_UNWRITABLE)
