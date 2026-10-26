"""Editable message blocks: only allow-listed placeholders, sane lengths (ADR 0009, 0022)."""

import pytest

from app.domain.channels import ChannelKind
from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import (
    BLOCKS_BY_KEY,
    MAX_SMS_LENGTH,
    MESSAGE_BLOCKS,
    clean_text,
    used_placeholders,
)


def test_every_purpose_has_an_email_subject_and_intro() -> None:
    for purpose in ("confirmation", "change", "reminder", "cancellation"):
        assert f"message.{purpose}.subject" in BLOCKS_BY_KEY
        assert f"message.{purpose}.intro" in BLOCKS_BY_KEY
    assert BLOCKS_BY_KEY["message.reminder.sms"].channel is ChannelKind.SMS
    assert len(BLOCKS_BY_KEY) == len(MESSAGE_BLOCKS)


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("Hallo {first_name}", frozenset({"first_name"})),
        ("{date} om {time}", frozenset({"date", "time"})),
        ("Accolades {{zo}}", frozenset()),
        ("geen", frozenset()),
        ("{x.__class__}", None),
        ("{date", None),
        ("date}", None),
        ("{}", None),
        ("{Date}", None),
    ],
)
def test_used_placeholders(text: str, names: frozenset[str] | None) -> None:
    assert used_placeholders(text) == names


def test_clean_text_trims_and_normalises_line_breaks() -> None:
    assert clean_text("message.reminder.intro", "  Regel 1\r\nRegel 2 ") == "Regel 1\nRegel 2"


def test_a_text_may_leave_placeholders_out() -> None:
    assert clean_text("message.reminder.subject", "Herinnering") == "Herinnering"


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("message.reminder.subject", ""),
        ("message.reminder.subject", "Herinnering\nBcc: x@evil.nl"),
        ("message.reminder.subject", "x" * 201),
        ("message.reminder.intro", "Hallo {first_name}"),  # not on the email allow-list
        ("message.reminder.intro", "{group_name.__class__}"),
        ("message.reminder.intro", "bel\x07"),
        ("message.reminder.sms", "x" * (MAX_SMS_LENGTH + 1)),
        ("message.signoff", "Groet {date}"),
    ],
)
def test_invalid_texts_name_the_block(key: str, text: str) -> None:
    with pytest.raises(DomainError) as error:
        clean_text(key, text)
    assert (error.value.code, error.value.field) == (ErrorCode.INVALID_CONFIGURATION, key)


def test_sms_may_carry_the_manage_link() -> None:
    text = "{first_name}, uw gesprek is {date} om {time}. Wijzigen: {manage_url}"
    assert clean_text("message.reminder.sms", text) == text


def test_unknown_block() -> None:
    with pytest.raises(DomainError) as error:
        clean_text("message.greeting", "Hoi")
    assert error.value.field == "key"
