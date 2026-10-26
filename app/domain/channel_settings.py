"""How an organisation delivers messages: its SMTP server and its SMS gateway (ADR 0022).

Each organisation configures its own channels, so mail comes from its own sender (HANDOVER
§4) and SMS goes through its own Webex Connect service (HANDOVER B6). Only the superuser may
change them (``integrations.manage``, HANDOVER §6).

The settings here are the non-secret part. The secret (SMTP password, Connect service key) is
passed alongside on save, stored encrypted by the adapter (ADR 0008), never read back into the
domain and never shown again: the domain only learns *whether* one is set.

    SmtpSettings   host, port, security, username, sender      secret: password (optional)
    SmsSettings    API base URL, sender ID, country code       secret: service key (required)
"""

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import formataddr
from enum import StrEnum
from urllib.parse import urlsplit

from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode

MAX_HOST_LENGTH = 253
MAX_USERNAME_LENGTH = 254
MAX_SENDER_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
MAX_URL_LENGTH = 200
MAX_SECRET_LENGTH = 512

CONFIGURABLE_CHANNELS = (ChannelKind.EMAIL, ChannelKind.SMS)
"""Channels an organisation can configure; WhatsApp waits for the privacy officer (ADR 0009)."""

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST = re.compile(rf"^(?=.{{1,{MAX_HOST_LENGTH}}}$){_LABEL}(?:\.{_LABEL})*$")
"""A host name or IPv4 address: dot-separated labels of letters, digits and inner hyphens."""
_ALPHANUMERIC_SENDER = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9 ]{1,11}$")
_NUMERIC_SENDER = re.compile(r"^\+?[0-9]{3,15}$")
_COUNTRY_CODE = re.compile(r"^[1-9][0-9]{0,2}$")


def _invalid(field: str) -> DomainError:
    """Return the error for an invalid channel setting ``field`` (never with its value)."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def _check_text(value: str | None, field: str, max_length: int) -> None:
    """Refuse control characters and texts longer than ``max_length``."""
    if value is not None and (len(value) > max_length or _CONTROL_CHARS.search(value)):
        raise _invalid(field)


def _check_email(value: str, field: str) -> None:
    """Basic structural check of an address (the SMTP server has the last word)."""
    local, at, domain = value.partition("@")
    if (
        not at
        or not local
        or "@" in domain
        or "." not in domain.strip(".")
        or len(value) > MAX_EMAIL_LENGTH
        or any(ch.isspace() or ch in '<>",;' for ch in value)
        or _CONTROL_CHARS.search(value)
    ):
        raise _invalid(field)


class SmtpSecurity(StrEnum):
    """How the connection to the SMTP server is secured."""

    STARTTLS = "starttls"
    TLS = "tls"
    NONE = "none"


@dataclass(frozen=True)
class SmtpSettings:
    """The organisation's mail server and sender.

    Attributes:
        host: Host name or IPv4 address of the SMTP server.
        port: TCP port (587 for STARTTLS, 465 for implicit TLS, 25 without).
        security: STARTTLS, implicit TLS or none (only for a relay on a trusted network,
            and never with a login).
        username: Login name; ``None`` when the server accepts mail without login.
        sender_address: The ``From`` address citizens see and can reply to.
        sender_name: Display name in ``From``; ``None`` shows the address only.

    Raises:
        DomainError: ``invalid_configuration`` naming the offending field.
    """

    host: str
    port: int
    security: SmtpSecurity
    username: str | None
    sender_address: str
    sender_name: str | None = None

    def __post_init__(self) -> None:
        """Validate every field."""
        if not _HOST.fullmatch(self.host):
            raise _invalid("smtp_host")
        if not 1 <= self.port <= 65535:
            raise _invalid("smtp_port")
        _check_text(self.username, "smtp_username", MAX_USERNAME_LENGTH)
        if self.username is not None and not self.username.strip():
            raise _invalid("smtp_username")
        if self.username is not None and self.security is SmtpSecurity.NONE:
            raise _invalid("smtp_security")  # a login would send the password in clear text
        _check_email(self.sender_address, "sender_address")
        _check_text(self.sender_name, "sender_name", MAX_SENDER_NAME_LENGTH)
        if self.sender_name is not None and not self.sender_name.strip():
            raise _invalid("sender_name")

    @property
    def channel(self) -> ChannelKind:
        """The channel these settings serve."""
        return ChannelKind.EMAIL

    @property
    def sender(self) -> str:
        """The ``From`` header, e.g. ``Gemeente Demo <afspraak@demo.nl>``."""
        return formataddr((self.sender_name or "", self.sender_address))

    @property
    def needs_secret(self) -> bool:
        """Whether a password must be stored: only when logging in."""
        return self.username is not None


@dataclass(frozen=True)
class SmsSettings:
    """The organisation's Webex Connect SMS service (HANDOVER B6).

    Attributes:
        api_base_url: The tenant's API host, e.g. ``https://api.us.webexconnect.io``; https
            only, without credentials, query or fragment.
        sender_id: Alphanumeric sender (at most 11 letters, digits or spaces, at least one
            letter) or a phone number registered for the service.
        country_code: Calling code for numbers entered without one (``31`` for 06-numbers).

    Raises:
        DomainError: ``invalid_configuration`` naming the offending field.
    """

    api_base_url: str
    sender_id: str
    country_code: str = "31"

    def __post_init__(self) -> None:
        """Validate every field."""
        parts = urlsplit(self.api_base_url)
        if (
            len(self.api_base_url) > MAX_URL_LENGTH
            or parts.scheme != "https"
            or not parts.hostname
            or not _HOST.fullmatch(parts.hostname)
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or _CONTROL_CHARS.search(self.api_base_url)
        ):
            raise _invalid("sms_api_base_url")
        if not (
            _ALPHANUMERIC_SENDER.fullmatch(self.sender_id)
            or _NUMERIC_SENDER.fullmatch(self.sender_id)
        ):
            raise _invalid("sms_sender_id")
        if not _COUNTRY_CODE.fullmatch(self.country_code):
            raise _invalid("sms_country_code")

    @property
    def channel(self) -> ChannelKind:
        """The channel these settings serve."""
        return ChannelKind.SMS

    @property
    def needs_secret(self) -> bool:
        """The service key is always required."""
        return True


ChannelConfig = SmtpSettings | SmsSettings
"""The settings of one configurable channel."""


@dataclass(frozen=True)
class ChannelSettings:
    """A stored channel configuration as the domain and the admin portal see it.

    Attributes:
        config: The non-secret settings.
        secret_set: Whether a password or service key is stored (its value never is here).
        updated_at: When the settings or the secret last changed.
    """

    config: ChannelConfig
    secret_set: bool
    updated_at: datetime

    @property
    def channel(self) -> ChannelKind:
        """The channel these settings serve."""
        return self.config.channel

    @property
    def ready(self) -> bool:
        """Whether messages can be sent with these settings."""
        return self.secret_set or not self.config.needs_secret


def secret_field(channel: ChannelKind) -> str:
    """Return the name of the channel's secret: ``smtp_credential`` or ``sms_service_key``."""
    return "smtp_credential" if channel is ChannelKind.EMAIL else "sms_service_key"


def validate_secret(secret: str, channel: ChannelKind) -> None:
    """Check a password or service key before it is stored.

    Raises:
        DomainError: ``invalid_configuration`` with ``secret_field(channel)``.
    """
    if not secret.strip() or len(secret) > MAX_SECRET_LENGTH or _CONTROL_CHARS.search(secret):
        raise _invalid(secret_field(channel))


def e164(phone: str, country_code: str) -> str:
    """Return ``phone`` in E.164 (``+31612345678``), as SMS gateways require.

    Booking phone numbers are stored as digits with an optional ``+`` (``00`` already became
    ``+``). A number without ``+`` is national: its leading trunk ``0`` is replaced by the
    organisation's ``country_code``.
    """
    if phone.startswith("+"):
        return phone
    return f"+{country_code}{phone.removeprefix('0')}"
