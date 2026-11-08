"""An organisation's mail server and SMS gateway in a configuration document (ADR 0022, 0099).

Two single objects of an organisation, ``email`` and ``sms``, both stored in
``channel_settings``. Their fields are the typed fields of ``SmtpSettings`` and
``SmsSettings``, and the rules are the ones ``ChannelSettingsService.save`` applies: the
settings classes validate every field, mail without TLS is refused in production unless the
host allows a plaintext relay, a given secret passes ``validate_secret``, and a stored secret
is kept only while it still belongs to the same server (or provider and host), because a
password is never sent to a host it was not entered for (ADR 0028).
"""

from collections.abc import Mapping

from app.domain import configuration_v1_names as names
from app.domain.authorization import Permission
from app.domain.channel_settings import (
    SmsProvider,
    SmsSettings,
    SmtpSecurity,
    SmtpSettings,
    check_smtp_transport,
    secret_field,
    validate_secret,
)
from app.domain.channels import ChannelKind
from app.domain.configuration_changes import IntegrityRule, RuleContext
from app.domain.configuration_checks import text_of
from app.domain.configuration_schema import Collection, Kind, Shape, integer, secret, text
from app.domain.configuration_values import Secret, Value
from app.domain.errors import DomainError, ErrorCode

EMAIL_KIND = names.EMAIL_CHANNEL_KIND
SMS_KIND = names.SMS_CHANNEL_KIND
"""The kind names, which other slices read by name."""

_TABLE = "channel_settings"
_SECRET_COLUMNS = ("secret_nonce", "secret_ciphertext")
_DERIVED = frozenset({f"{_TABLE}.channel", f"{_TABLE}.updated_at"})


def _blank_to_none(values: Mapping[str, Value], name: str) -> str | None:
    """Return an optional text field, ``None`` when it is absent or blank (as the form does)."""
    value = values.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _checked_secret(
    merged: Mapping[str, Value],
    current: Mapping[str, Value] | None,
    channel: ChannelKind,
    *,
    needed: bool,
    binding_changed: bool,
) -> None:
    """Apply the secret rules of a channel.

    A secret given in the document is validated (when it is opened). A channel that needs one
    cannot be saved without; one that is only kept from the stored value must still belong to
    the same server.

    Raises:
        DomainError: ``invalid_configuration`` with ``smtp_credential`` or ``sms_api_key``.
    """
    value = merged.get("secret")
    if isinstance(value, Secret):
        validate_secret(value.reveal(), channel)
    if not needed:
        return
    kept = current is not None and value is not None and value == current.get("secret")
    if value is None or (kept and binding_changed):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, secret_field(channel))


def _smtp(values: Mapping[str, Value]) -> SmtpSettings:
    """Build the SMTP settings the fields state; the class validates them.

    Raises:
        DomainError: ``invalid_configuration`` naming the field.
    """
    try:
        security = SmtpSecurity(text_of(values, "security"))
    except ValueError:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "smtp_security") from None
    port = values.get("port")
    return SmtpSettings(
        host=text_of(values, "host"),
        port=port if isinstance(port, int) else 0,
        security=security,
        username=_blank_to_none(values, "username"),
        sender_address=text_of(values, "sender_address"),
        sender_name=_blank_to_none(values, "sender_name"),
        reply_to_address=_blank_to_none(values, "reply_to_address"),
        dkim_selector=_blank_to_none(values, "dkim_selector"),
    )


def _email_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check the mail channel as the admin screen does; return its normalised fields."""
    config = _smtp(merged)
    check_smtp_transport(
        config,
        production=context.production,
        plaintext_allowed=context.smtp_plaintext_relay_allowed,
    )
    old = _stored_smtp(current)
    _checked_secret(
        merged,
        current,
        ChannelKind.EMAIL,
        needed=config.needs_secret,
        binding_changed=old is None or old.secret_binding != config.secret_binding,
    )
    return {
        **merged,
        "host": config.host,
        "port": config.port,
        "security": config.security.value,
        "username": config.username,
        "sender_address": config.sender_address,
        "sender_name": config.sender_name,
        "reply_to_address": config.reply_to_address,
        "dkim_selector": config.dkim_selector,
    }


def _stored_smtp(current: Mapping[str, Value] | None) -> SmtpSettings | None:
    """Return the stored mail settings, ``None`` when there are none or they no longer pass."""
    if current is None:
        return None
    try:
        return _smtp(current)
    except DomainError:
        return None


def _country_code(value: Value, context: RuleContext) -> Value:
    """Return the calling code without a leading ``+``, as the form stores it."""
    return str(value).replace("+", "")


def _sms(values: Mapping[str, Value]) -> SmsSettings:
    """Build the SMS settings the fields state; the class validates them.

    Raises:
        DomainError: ``invalid_configuration`` naming the field.
    """
    try:
        provider = SmsProvider(text_of(values, "provider"))
    except ValueError:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "sms_provider") from None
    return SmsSettings(
        provider=provider,
        api_base_url=text_of(values, "api_base_url"),
        sender_id=text_of(values, "sender_id"),
        country_code=text_of(values, "country_code"),
    )


def _stored_sms(current: Mapping[str, Value] | None) -> SmsSettings | None:
    """Return the stored SMS settings, ``None`` when there are none or they no longer pass."""
    if current is None:
        return None
    try:
        return _sms(current)
    except DomainError:
        return None


def _sms_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check the SMS channel as the admin screen does; return its normalised fields."""
    config = _sms(merged)
    old = _stored_sms(current)
    _checked_secret(
        merged,
        current,
        ChannelKind.SMS,
        needed=True,
        binding_changed=old is None or old.secret_binding != config.secret_binding,
    )
    return {
        **merged,
        "provider": config.provider.value,
        "api_base_url": config.api_base_url,
        "sender_id": config.sender_id,
        "country_code": config.country_code,
    }


EMAIL = Kind(
    EMAIL_KIND,
    permission=Permission.INTEGRATIONS_MANAGE,
    tables=frozenset({_TABLE}),
    fields=(
        text("host", required=True, max_length=253, columns=("settings",)),
        integer("port", default=587, minimum=1, maximum=65535),
        text("security", choices=tuple(s.value for s in SmtpSecurity), default="starttls"),
        text("username", nullable=True, max_length=254),
        text("sender_address", required=True, max_length=254),
        text("sender_name", nullable=True, max_length=100),
        text("reply_to_address", nullable=True, max_length=254),
        text("dkim_selector", nullable=True, max_length=253),
        secret("secret", columns=_SECRET_COLUMNS),
    ),
    check=_email_check,
    derived=_DERIVED,
    description="The organisation's SMTP server and sender (ADR 0022, 0073).",
)

SMS = Kind(
    SMS_KIND,
    permission=Permission.INTEGRATIONS_MANAGE,
    tables=frozenset({_TABLE}),
    fields=(
        text("provider", choices=tuple(p.value for p in SmsProvider), default="webex_connect"),
        text("api_base_url", required=True, max_length=200, columns=("settings",)),
        text("sender_id", required=True, max_length=15),
        text("country_code", default="31", max_length=5, check=_country_code),
        secret("secret", columns=_SECRET_COLUMNS),
    ),
    check=_sms_check,
    derived=_DERIVED,
    description="The organisation's SMS gateway (ADR 0022).",
)

EMAIL_CHANNEL = Collection("email", EMAIL, Shape.SINGLE, "The mail server and sender.")
SMS_CHANNEL = Collection("sms", SMS, Shape.SINGLE, "The SMS gateway.")
"""The ``email`` and ``sms`` sections of an organisation."""

INTEGRITY: tuple[IntegrityRule, ...] = ()
"""No rule across objects: a channel belongs to one organisation and nothing refers to it."""
