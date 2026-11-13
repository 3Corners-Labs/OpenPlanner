"""The rules of the inbound webhook gate (ADR 0124): reasons, statuses, size and media type."""

import pytest

from app.domain.inbound_gate import (
    ACCEPTED_MEDIA_TYPES,
    MAX_BODY_BYTES,
    STATUS,
    Refusal,
    accepts_content_type,
    declared_length_acceptable,
)


def test_the_list_of_reasons_is_the_closed_list_of_the_decision() -> None:
    assert [r.value for r in Refusal] == [
        "unknown_address",
        "network",
        "source_unknown",
        "rate",
        "content_type",
        "too_large",
        "invalid_signature",
        "stale",
        "malformed",
    ]


def test_every_reason_has_the_status_of_the_decision() -> None:
    assert {r: STATUS[r] for r in Refusal} == {
        Refusal.UNKNOWN_ADDRESS: 404,
        Refusal.NETWORK: 403,
        Refusal.SOURCE_UNKNOWN: 403,
        Refusal.RATE: 429,
        Refusal.CONTENT_TYPE: 415,
        Refusal.TOO_LARGE: 413,
        Refusal.SIGNATURE: 401,
        Refusal.STALE: 401,
        Refusal.MALFORMED: 400,
    }
    assert Refusal.SIGNATURE.status == 401


def test_the_size_limit_is_64_kib() -> None:
    assert MAX_BODY_BYTES == 64 * 1024


@pytest.mark.parametrize(
    "value",
    [
        "application/json",
        "Application/JSON",
        "application/json; charset=utf-8",
        "application/json;charset=UTF-8",
        " application/json ",
        "application/cloudevents+json",
        "application/cloudevents+json; charset=utf-8",
        None,
        "",
    ],
)
def test_json_media_types_and_no_header_at_all_are_accepted(value: str) -> None:
    assert accepts_content_type(value)


@pytest.mark.parametrize(
    "value",
    [
        "text/plain",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "application/jsonp",
        "application/json-seq",
        "application/cloudevents-batch+json",
        "text/json",
        ";application/json",
    ],
)
def test_a_header_naming_anything_else_is_refused(value: str) -> None:
    assert not accepts_content_type(value)


def test_the_accepted_media_types_are_the_two_json_types() -> None:
    assert ACCEPTED_MEDIA_TYPES == frozenset({"application/json", "application/cloudevents+json"})


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (None, True),
        ("", True),
        ("chunked", True),
        ("abc", True),
        ("-5", True),
        ("1.5", True),
        ("²", True),
        ("0", True),
        (str(MAX_BODY_BYTES), True),
        (f" {MAX_BODY_BYTES} ", True),
        (str(MAX_BODY_BYTES + 1), False),
        ("99999999999999999999", False),
    ],
)
def test_only_a_declared_length_over_the_limit_is_refused(
    value: str | None, accepted: bool
) -> None:
    assert declared_length_acceptable(value) is accepted


def test_the_limit_can_be_given() -> None:
    assert declared_length_acceptable("10", 10)
    assert not declared_length_acceptable("11", 10)


def test_a_length_too_long_to_be_a_number_is_refused_not_an_error() -> None:
    # Python refuses to convert a digit string of thousands of characters; the rule must
    # still answer, and the answer is "over the limit".
    assert not declared_length_acceptable("9" * 5000)
    assert not declared_length_acceptable("1" + "0" * 30, 10)
    assert declared_length_acceptable("0" * 5000 + "7", 10)
