"""The mail server and SMS gateway of an organisation in a document (ADR 0022, 0099)."""

from typing import Any

import pytest

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import ChangeAction, RuleContext
from app.domain.configuration_document import to_json
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_channels import (
    EMAIL,
    EMAIL_CHANNEL,
    INTEGRITY,
    SMS,
    SMS_CHANNEL,
)
from app.domain.configuration_values import Secret
from app.domain.errors import ErrorCode
from tests.support.configuration import Recorder, entity, parse
from tests.support.configuration_columns import uncovered_columns
from tests.support.configuration_parts import installation, organisation, plan_opened, stub_format

FMT = stub_format((EMAIL_CHANNEL, SMS_CHANNEL), (), *INTEGRITY)
SECRET = "aHVudGVyMi1zZWNyZXQ="  # base64 text; the test opens it as the plain secret
EMAIL_PATH = "organisations[acme].email"
SMS_PATH = "organisations[acme].sms"


def mail(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "host": "smtp.acme.nl",
        "port": 587,
        "security": "starttls",
        "username": "mailer",
        "sender_address": "afspraak@acme.nl",
        "secret": {"sealed": SECRET},
    }
    values.update(overrides)
    return values


def sms(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "api_base_url": "https://api.us.webexconnect.io",
        "sender_id": "Acme",
        "secret": {"sealed": SECRET},
    }
    values.update(overrides)
    return values


def doc(**channels: dict[str, Any]) -> dict[str, Any]:
    return {"organisations": [{"slug": "acme", **channels}]}


def stored_mail(**overrides: Any) -> Entity:
    fields: dict[str, Any] = {
        "host": "smtp.acme.nl",
        "port": 587,
        "security": "starttls",
        "username": "mailer",
        "sender_address": "afspraak@acme.nl",
        "sender_name": None,
        "reply_to_address": None,
        "dkim_selector": None,
        "secret": Secret(SECRET),
    }
    fields.update(overrides)
    return entity("email_channel", (), fields)


def stored_sms(**overrides: Any) -> Entity:
    fields: dict[str, Any] = {
        "provider": "webex_connect",
        "api_base_url": "https://api.us.webexconnect.io",
        "sender_id": "Acme",
        "country_code": "31",
        "secret": Secret(SECRET),
    }
    fields.update(overrides)
    return entity("sms_channel", (), fields)


def current(**channels: tuple[Entity, ...]) -> Entity:
    return installation(organisation("acme", **channels))


def refusals(result: Any) -> list[tuple[str, ErrorCode, str | None]]:
    return [(r.path, r.code, r.field) for r in result.refusals]


def test_a_mail_server_is_created_with_the_defaults_and_the_integrations_permission() -> None:
    recorder = Recorder()

    result = plan_opened(doc(email=mail()), current(), FMT, may_change=recorder)

    (change,) = result.changes
    assert result.ok
    assert (change.kind, change.address, change.action) == (
        "email_channel",
        ("acme",),
        ChangeAction.CREATE,
    )
    assert change.values["security"] == "starttls"
    assert change.values["sender_name"] is None
    assert change.permissions == {Permission.INTEGRATIONS_MANAGE}
    assert recorder.asked == [(Permission.INTEGRATIONS_MANAGE, Target.for_organisation("acme"))]


def test_blank_optional_texts_become_null_as_the_form_does() -> None:
    data = doc(email=mail(sender_name="  ", reply_to_address="", dkim_selector=" "))

    (change,) = plan_opened(data, current(), FMT).changes

    assert change.values["sender_name"] is None
    assert change.values["reply_to_address"] is None
    assert change.values["dkim_selector"] is None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"host": "bad host!"}, "smtp_host"),
        ({"sender_address": "no-at"}, "sender_address"),
        ({"reply_to_address": "x"}, "reply_to_address"),
        ({"dkim_selector": "bad selector"}, "dkim_selector"),
        ({"security": "none"}, "smtp_security"),
    ],
)
def test_a_mail_server_the_screen_would_refuse_is_refused_by_field(
    overrides: dict[str, Any], field: str
) -> None:
    result = plan_opened(doc(email=mail(**overrides)), current(), FMT)

    assert refusals(result) == [(EMAIL_PATH, ErrorCode.INVALID_CONFIGURATION, field)]


def test_mail_without_encryption_needs_the_host_to_allow_a_plaintext_relay() -> None:
    relay = mail(security="none", username=None, secret=None)
    data = doc(email=relay)

    development = plan_opened(data, current(), FMT)
    production = plan_opened(data, current(), FMT, context=RuleContext(production=True))
    allowed = plan_opened(
        data,
        current(),
        FMT,
        context=RuleContext(production=True, smtp_plaintext_relay_allowed=True),
    )

    assert development.ok
    assert refusals(production) == [(EMAIL_PATH, ErrorCode.INVALID_CONFIGURATION, "smtp_security")]
    assert allowed.ok


def test_a_login_needs_a_password_and_a_relay_without_login_needs_none() -> None:
    without_secret = {k: v for k, v in mail().items() if k != "secret"}

    refused = plan_opened(doc(email=without_secret), current(), FMT)
    relay = plan_opened(doc(email={**without_secret, "username": None}), current(), FMT)

    assert refusals(refused) == [(EMAIL_PATH, ErrorCode.INVALID_CONFIGURATION, "smtp_credential")]
    assert relay.ok


def test_a_given_password_is_checked_like_the_screen_checks_it() -> None:
    result = plan_opened(doc(email=mail(secret={"sealed": "QUFB" * 150})), current(), FMT)

    assert refusals(result) == [(EMAIL_PATH, ErrorCode.INVALID_CONFIGURATION, "smtp_credential")]


def test_the_stored_password_is_kept_only_for_the_same_server() -> None:
    tree = current(email=(stored_mail(),))
    same = doc(email={"sender_name": "Acme"})
    moved = doc(email={"host": "smtp.other.nl"})
    moved_with_secret = doc(
        email={"host": "smtp.other.nl", "secret": {"sealed": "bmV3LXNlY3JldA=="}}
    )

    kept = plan_opened(same, tree, FMT)
    refused = plan_opened(moved, tree, FMT)
    given = plan_opened(moved_with_secret, tree, FMT)

    assert kept.ok
    assert kept.changes[0].fields == ("sender_name",)
    assert refusals(refused) == [(EMAIL_PATH, ErrorCode.INVALID_CONFIGURATION, "smtp_credential")]
    assert given.ok


def test_the_mail_channel_can_be_removed_and_an_unchanged_one_changes_nothing() -> None:
    tree = current(email=(stored_mail(),))

    removed = plan_opened(doc(email={"action": "delete"}), tree, FMT)
    same = plan_opened(doc(email=mail()), tree, FMT)

    assert [(c.kind, c.action) for c in removed.changes] == [("email_channel", ChangeAction.DELETE)]
    assert same.changes == ()


def test_an_sms_gateway_is_created_and_its_country_code_loses_the_plus() -> None:
    (change,) = plan_opened(doc(sms=sms(country_code="+44")), current(), FMT).changes

    assert (change.kind, change.action) == ("sms_channel", ChangeAction.CREATE)
    assert change.values["country_code"] == "44"
    assert change.values["provider"] == "webex_connect"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"api_base_url": "http://api.example.com"}, "sms_api_base_url"),
        ({"sender_id": "1"}, "sms_sender_id"),
        ({"country_code": "0031"}, "sms_country_code"),
    ],
)
def test_an_sms_gateway_the_screen_would_refuse_is_refused_by_field(
    overrides: dict[str, Any], field: str
) -> None:
    result = plan_opened(doc(sms=sms(**overrides)), current(), FMT)

    assert refusals(result) == [(SMS_PATH, ErrorCode.INVALID_CONFIGURATION, field)]


def test_an_sms_gateway_always_needs_its_api_key() -> None:
    without = {k: v for k, v in sms().items() if k != "secret"}

    result = plan_opened(doc(sms=without), current(), FMT)

    assert refusals(result) == [(SMS_PATH, ErrorCode.INVALID_CONFIGURATION, "sms_api_key")]


def test_the_api_key_is_kept_only_for_the_same_provider_and_host() -> None:
    tree = current(sms=(stored_sms(),))

    kept = plan_opened(doc(sms={"sender_id": "Acme BV"}), tree, FMT)
    moved = plan_opened(doc(sms={"api_base_url": "https://api.eu.webexconnect.io"}), tree, FMT)

    assert kept.ok
    assert kept.changes[0].fields == ("sender_id",)
    assert refusals(moved) == [(SMS_PATH, ErrorCode.INVALID_CONFIGURATION, "sms_api_key")]


def test_a_kept_key_survives_a_stored_configuration_that_no_longer_passes() -> None:
    tree = current(sms=(stored_sms(api_base_url="http://old.example.com"),))

    result = plan_opened(doc(sms={"api_base_url": "https://api.us.webexconnect.io"}), tree, FMT)

    assert refusals(result) == [(SMS_PATH, ErrorCode.INVALID_CONFIGURATION, "sms_api_key")]


def test_both_channels_read_and_write_the_same_document() -> None:
    data = doc(
        email=mail(sender_name="Acme", reply_to_address="hulp@acme.nl", dkim_selector="s1"),
        sms=sms(country_code="31", provider="webex_connect"),
    )

    assert to_json(parse(data, FMT)) == {"format": "test/v1", **data}


def test_the_kinds_cover_every_column_of_their_table() -> None:
    assert uncovered_columns([EMAIL, SMS]) == {}
