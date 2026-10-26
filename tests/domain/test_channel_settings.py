"""Channel settings: what an SMTP server and an SMS gateway must look like (ADR 0022)."""

from datetime import UTC, datetime

import pytest

from app.domain.channel_settings import (
    ChannelSettings,
    SmsSettings,
    SmtpSecurity,
    SmtpSettings,
    e164,
    validate_secret,
)
from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode

AT = datetime(2026, 1, 1, tzinfo=UTC)


def smtp(**overrides: object) -> SmtpSettings:
    values: dict[str, object] = {
        "host": "smtp.demo.nl",
        "port": 587,
        "security": SmtpSecurity.STARTTLS,
        "username": "afspraak",
        "sender_address": "afspraak@demo.nl",
        "sender_name": "Gemeente Demo",
    }
    values.update(overrides)
    return SmtpSettings(**values)  # type: ignore[arg-type]


def sms(**overrides: object) -> SmsSettings:
    values: dict[str, object] = {
        "api_base_url": "https://api.us.webexconnect.io",
        "sender_id": "GemDemo",
        "country_code": "31",
    }
    values.update(overrides)
    return SmsSettings(**values)  # type: ignore[arg-type]


def field_of(error: pytest.ExceptionInfo[DomainError]) -> str | None:
    assert error.value.code is ErrorCode.INVALID_CONFIGURATION
    return error.value.field


def test_valid_smtp_settings_build_the_from_header() -> None:
    settings = smtp()
    assert settings.channel is ChannelKind.EMAIL
    assert settings.sender == "Gemeente Demo <afspraak@demo.nl>"
    assert smtp(sender_name=None).sender == "afspraak@demo.nl"
    assert settings.needs_secret
    assert not smtp(username=None).needs_secret


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"host": "smtp demo.nl"}, "smtp_host"),
        ({"host": "-smtp.demo.nl"}, "smtp_host"),
        ({"host": ""}, "smtp_host"),
        ({"port": 0}, "smtp_port"),
        ({"port": 65536}, "smtp_port"),
        ({"username": " "}, "smtp_username"),
        ({"username": "a\nb"}, "smtp_username"),
        ({"security": SmtpSecurity.NONE}, "smtp_security"),  # no password in clear text
        ({"sender_address": "no-at-sign"}, "sender_address"),
        ({"sender_address": "a@b"}, "sender_address"),
        ({"sender_address": "x@demo.nl\r\nBcc: y@evil.nl"}, "sender_address"),
        ({"sender_address": "<x@demo.nl>"}, "sender_address"),
        ({"sender_name": "Demo\r\nBcc: y@evil.nl"}, "sender_name"),
        ({"sender_name": "  "}, "sender_name"),
        ({"sender_name": "x" * 101}, "sender_name"),
    ],
)
def test_invalid_smtp_settings_name_the_field(overrides: dict[str, object], field: str) -> None:
    with pytest.raises(DomainError) as error:
        smtp(**overrides)
    assert field_of(error) == field


def test_valid_sms_settings() -> None:
    settings = sms()
    assert settings.channel is ChannelKind.SMS
    assert settings.needs_secret
    assert sms(sender_id="+31612345678").sender_id == "+31612345678"
    assert sms(api_base_url="https://api.eu.webexconnect.io/").api_base_url.startswith("https")


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"api_base_url": "http://api.us.webexconnect.io"}, "sms_api_base_url"),
        ({"api_base_url": "https://user:pw@api.us.webexconnect.io"}, "sms_api_base_url"),
        ({"api_base_url": "https://api.us.webexconnect.io/?x=1"}, "sms_api_base_url"),
        ({"api_base_url": "https://api.us.webexconnect.io/#x"}, "sms_api_base_url"),
        ({"api_base_url": "https:///path"}, "sms_api_base_url"),
        ({"api_base_url": "https://" + "a" * 200 + ".nl"}, "sms_api_base_url"),
        ({"sender_id": "TooLongSender"}, "sms_sender_id"),
        ({"sender_id": "12"}, "sms_sender_id"),
        ({"sender_id": "Gem-Demo"}, "sms_sender_id"),
        ({"sender_id": ""}, "sms_sender_id"),
        ({"country_code": "031"}, "sms_country_code"),
        ({"country_code": "1234"}, "sms_country_code"),
    ],
)
def test_invalid_sms_settings_name_the_field(overrides: dict[str, object], field: str) -> None:
    with pytest.raises(DomainError) as error:
        sms(**overrides)
    assert field_of(error) == field


def test_ready_needs_the_secret_only_where_it_is_required() -> None:
    assert not ChannelSettings(sms(), secret_set=False, updated_at=AT).ready
    assert ChannelSettings(sms(), secret_set=True, updated_at=AT).ready
    assert ChannelSettings(smtp(username=None), secret_set=False, updated_at=AT).ready
    assert ChannelSettings(sms(), True, AT).channel is ChannelKind.SMS


@pytest.mark.parametrize(
    ("secret", "channel", "field"),
    [
        ("   ", ChannelKind.EMAIL, "smtp_credential"),
        ("x" * 513, ChannelKind.SMS, "sms_service_key"),
        ("key\n", ChannelKind.SMS, "sms_service_key"),
    ],
)
def test_invalid_secrets(secret: str, channel: ChannelKind, field: str) -> None:
    with pytest.raises(DomainError) as error:
        validate_secret(secret, channel)
    assert field_of(error) == field


def test_valid_secret() -> None:
    validate_secret("s3cr3t key", ChannelKind.EMAIL)


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("+31612345678", "+31612345678"),
        ("0612345678", "+31612345678"),
        ("612345678", "+31612345678"),
        ("+3225551234", "+3225551234"),
    ],
)
def test_e164(phone: str, expected: str) -> None:
    assert e164(phone, "31") == expected
