"""Log destinations: where copies of the log lines go (ADR 0085 part 1).

A destination is a copy of the lines on stdout, sent to a log platform: syslog, Splunk HEC,
Elastic or any HTTP endpoint that takes JSON lines. This module is the pure side of it: the
object, its validation, the rule which line a destination receives and the status the sender
reports. Nothing here opens a socket; the senders are adapters behind ``LogDestinationSender``
(``app.domain.ports``).

    line (JSON, masked) ─► accepts(destination, record)? ─► queue ─► batch ─► sender

Which line a destination receives (point 3 of the ADR):

    audit, system, security events   only when their category is ticked; the level does not
                                     apply, as on stdout the audit trail bypasses LOG_LEVEL
    diagnostic events                only with ``include_diagnostic``
    trace lines                      only with ``include_trace``, and at least ``min_level``
    every other line                 at least ``min_level``

A destination narrows the process's own level, it never widens it: a ``trace`` destination
sees trace lines only while ``LOG_LEVEL`` is ``TRACE`` too.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from app.domain.audit import Category
from app.domain.errors import DomainError, ErrorCode

LEVELS = ("trace", "debug", "info", "warning", "error", "critical")
"""The ``log.level`` values in order, lowest first (ADR 0040 part 2 and ADR 0085 point 8)."""

SHIPPED_CATEGORIES = (Category.AUDIT, Category.SYSTEM, Category.SECURITY)
"""The audit-line categories a destination may tick (ADR 0034: diagnostic is its own switch)."""

MAX_NAME = 80
MAX_ADDRESS = 500
MAX_TARGET = 120
MAX_CA_BYTES = 65_536
"""Bounds of the free-text fields of a draft."""

_TARGET_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:*")
"""What an index, source or data stream name may hold (no spaces, slashes or quotes)."""

TEST_MESSAGE = "logging.test"
"""The ``message`` of the line *Send test line* sends (ADR 0085 point 5)."""

AUDIT_ACTION = "logging.destination"
"""Audit action of a change; the detail says ``created|changed|deleted|enabled|disabled|tested``."""

HEALTH_EVENT = "logging.destination"
"""System event action when a destination starts failing or recovers (ADR 0085 point 4)."""


class DestinationKind(StrEnum):
    """The closed list of destination kinds (ADR 0085 point 2)."""

    SYSLOG = "syslog"
    """RFC 5424 over TLS (RFC 5425 framing), TCP or UDP."""
    SPLUNK_HEC = "splunk_hec"
    """Splunk HTTP Event Collector."""
    ELASTIC = "elastic"
    """Elasticsearch or OpenSearch ``_bulk`` into a data stream."""
    HTTP = "http"
    """NDJSON ``POST`` to one URL, optional bearer token."""

    @property
    def has_credential(self) -> bool:
        """Whether the kind takes a secret: the HEC token, API key or bearer token."""
        return self is not DestinationKind.SYSLOG

    @property
    def needs_credential(self) -> bool:
        """Whether the kind cannot work without one."""
        return self in (DestinationKind.SPLUNK_HEC, DestinationKind.ELASTIC)

    @property
    def schemes(self) -> tuple[str, ...]:
        """The address schemes the kind accepts, safest first."""
        if self is DestinationKind.SYSLOG:
            return ("syslog+tls", "syslog+tcp", "syslog+udp")
        return ("https", "http")


class State(StrEnum):
    """How the sender of a destination is doing."""

    IDLE = "idle"
    """Nothing sent yet since the process started."""
    OK = "ok"
    FAILING = "failing"


class Reason(StrEnum):
    """Why a delivery failed; machine codes, never the platform's own text (ADR 0085 point 4)."""

    UNREACHABLE = "unreachable"
    """No connection: DNS, refused or reset, or a timeout."""
    REFUSED = "refused"
    """The platform answered and said no: authentication or a client error."""
    TLS = "tls"
    """The server certificate was not trusted or the handshake failed."""
    SERVER = "server"
    """The platform answered with a server error."""
    NOT_READY = "not_ready"
    """No credential could be opened: the settings key is missing."""


class DeliveryError(Exception):
    """A delivery failed; ``reason`` is the machine code, never the platform's own text.

    Args:
        reason: Why it failed.
        code: The platform's status code or the syslog error class, for the page; no body.
    """

    def __init__(self, reason: Reason, code: int | None = None) -> None:
        """Store the reason and the optional code."""
        super().__init__(reason.value)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class LogDestination:
    """One configured destination.

    Attributes:
        id: Database id; ``None`` before the first save.
        name: Short name shown in the list, unique.
        kind: The kind of platform.
        address: ``https://host/path``, or ``syslog+tls://host:port`` and its relatives.
        index: Splunk index, or Elastic data stream; empty for the other kinds.
        source: Splunk source, or syslog hostname; empty for the other kinds.
        min_level: The lowest ``log.level`` of an ordinary line.
        categories: The audit-line categories this destination receives.
        include_diagnostic: Receives diagnostic events (ADR 0040 part 4), which never go to
            stdout.
        include_trace: Receives trace lines.
        enabled: Whether the senders deliver.
        secret_set: Whether a credential is stored; the value is never read back.
        ca_pem: Extra certificate authorities to trust for this destination (public PEM).
        created_at: When it was added.
        updated_at: When it last changed.
        tested_at: When *Send test line* last succeeded; ``None`` means untested.
    """

    id: int | None
    name: str
    kind: DestinationKind
    address: str
    index: str
    source: str
    min_level: str
    categories: frozenset[Category]
    include_diagnostic: bool
    include_trace: bool
    enabled: bool
    secret_set: bool
    ca_pem: str
    created_at: datetime
    updated_at: datetime
    tested_at: datetime | None = None


@dataclass(frozen=True)
class DestinationDraft:
    """The fields an admin types; the rest is the destination's own state."""

    name: str
    kind: DestinationKind
    address: str
    index: str = ""
    source: str = ""
    min_level: str = "info"
    categories: frozenset[Category] = frozenset(SHIPPED_CATEGORIES)
    include_diagnostic: bool = False
    include_trace: bool = False
    ca_pem: str = ""


@dataclass(frozen=True)
class DestinationStatus:
    """What one process reports about one destination (ADR 0085 point 4).

    The web and the worker each keep their own queue, so there is one status per service.
    """

    destination_id: int
    service: str
    state: State = State.IDLE
    sent: int = 0
    dropped: int = 0
    failed: int = 0
    queued: int = 0
    last_ok_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Address:
    """A parsed destination address."""

    scheme: str
    host: str
    port: int | None
    path: str

    @property
    def secure(self) -> bool:
        """Whether the connection is encrypted."""
        return self.scheme in ("https", "syslog+tls")


def parse_address(kind: DestinationKind, address: str, *, production: bool) -> Address:
    """Check ``address`` for ``kind`` and split it.

    A syslog address needs a port; an HTTP one has the scheme's default. Plain ``http`` and
    unencrypted syslog are for development and test only, as with ``PUBLIC_BASE_URL``. A user
    name or password in the address is refused: credentials have their own sealed field.

    Raises:
        DomainError: ``log_destination_invalid`` with field ``address``.
    """
    text = address.strip()
    if not text or len(text) > MAX_ADDRESS or any(c.isspace() or ord(c) < 32 for c in text):
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "address")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "address") from exc
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in kind.schemes or not host or parts.username or parts.password:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "address")
    if parts.query or parts.fragment:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "address")
    if production and scheme in ("http", "syslog+tcp", "syslog+udp"):
        raise DomainError(ErrorCode.LOG_DESTINATION_INSECURE, "address")
    if kind is DestinationKind.SYSLOG and port is None:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "address")
    return Address(scheme, host, port, parts.path)


def _clean_target(value: str, field: str) -> str:
    """Return a trimmed index or source name; refuse anything but a plain token."""
    text = value.strip()
    if len(text) > MAX_TARGET or not set(text) <= _TARGET_CHARS:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, field)
    return text


def validate_draft(draft: DestinationDraft, *, production: bool) -> DestinationDraft:
    """Return the draft cleaned, or refuse it.

    The PEM is checked by the adapter that parses certificates; here only its size and shape
    (no private key) are, so a private key pasted by mistake never reaches the database.

    Raises:
        DomainError: ``log_destination_invalid`` (the field), ``log_destination_insecure``.
    """
    name = " ".join(draft.name.split())
    if not name or len(name) > MAX_NAME:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "name")
    parse_address(draft.kind, draft.address, production=production)
    if draft.min_level not in LEVELS:
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "min_level")
    if not draft.categories <= set(SHIPPED_CATEGORIES):
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "categories")
    pem = draft.ca_pem.strip()
    if pem and (
        len(pem.encode()) > MAX_CA_BYTES or "PRIVATE KEY" in pem or "BEGIN CERTIFICATE" not in pem
    ):
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "ca_pem")
    own_index = draft.kind in (DestinationKind.SPLUNK_HEC, DestinationKind.ELASTIC)
    own_source = draft.kind in (DestinationKind.SPLUNK_HEC, DestinationKind.SYSLOG)
    return DestinationDraft(
        name=name,
        kind=draft.kind,
        address=draft.address.strip(),
        index=_clean_target(draft.index, "index") if own_index else "",
        source=_clean_target(draft.source, "source") if own_source else "",
        min_level=draft.min_level,
        categories=draft.categories,
        include_diagnostic=draft.include_diagnostic,
        include_trace=draft.include_trace,
        ca_pem=pem,
    )


def validate_secret(kind: DestinationKind, secret: str) -> str:
    """Return the credential trimmed, or refuse it.

    Raises:
        DomainError: ``log_destination_invalid`` with field ``secret``.
    """
    text = secret.strip()
    if not kind.has_credential or not text or len(text) > 512 or any(c.isspace() for c in text):
        raise DomainError(ErrorCode.LOG_DESTINATION_INVALID, "secret")
    return text


def level_rank(level: object) -> int:
    """Return the position of ``level`` in ``LEVELS``; unknown values rank as ``info``."""
    return (
        LEVELS.index(level) if isinstance(level, str) and level in LEVELS else LEVELS.index("info")
    )


def accepts(destination: LogDestination, record: Mapping[str, object]) -> bool:
    """Return whether ``destination`` receives the line with this parsed ``record``."""
    category = record.get("event.category")
    if record.get("log_type") == "audit" or category is not None:
        if category == Category.DIAGNOSTIC:
            return destination.include_diagnostic
        return any(category == c for c in destination.categories)
    level = record.get("log.level")
    if level == "trace" and not destination.include_trace:
        return False
    return level_rank(level) >= level_rank(destination.min_level)


def categories_text(categories: Collection[Category]) -> str:
    """Return the categories as a stable, comma-separated string (for storage)."""
    return ",".join(sorted(str(c) for c in categories))


def parse_categories(text: str) -> frozenset[Category]:
    """Return the categories stored by ``categories_text``; unknown names are dropped."""
    wanted = {part for part in text.split(",") if part}
    return frozenset(c for c in SHIPPED_CATEGORIES if str(c) in wanted)
