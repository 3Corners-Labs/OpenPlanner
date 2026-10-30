"""Judging the certificate of the public address (ADR 0064)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.tls import (
    Address,
    Certificate,
    CertificateCheck,
    Finding,
    ProbeResult,
    Trust,
    judge,
    public_address,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
WARN = timedelta(days=20)


def certificate(days_left: int = 60, *names: str, since_days: int = 30) -> Certificate:
    return Certificate(
        "afspraak.example.nl",
        "Let's Encrypt (R11)",
        NOW - timedelta(days=since_days),
        NOW + timedelta(days=days_left),
        names or ("afspraak.example.nl",),
    )


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("https://afspraak.example.nl", Address("afspraak.example.nl", 443)),
        ("https://afspraak.example.nl/", Address("afspraak.example.nl", 443)),
        ("https://booking.test:8443", Address("booking.test", 8443)),
        ("http://127.0.0.1:8000", None),
        ("https://", None),
    ],
)
def test_the_public_address_is_probed_only_when_it_is_https(
    url: str, address: Address | None
) -> None:
    assert public_address(url) == address


def test_a_trusted_certificate_with_time_left_is_ok() -> None:
    result = ProbeResult(certificate(), Trust.TRUSTED)

    assert judge(result, "afspraak.example.nl", NOW, WARN) is Finding.OK


@pytest.mark.parametrize(("days_left", "finding"), [(19, Finding.EXPIRING), (20, Finding.OK)])
def test_fewer_days_left_than_the_warning_is_expiring(days_left: int, finding: Finding) -> None:
    result = ProbeResult(certificate(days_left), Trust.TRUSTED)

    assert judge(result, "afspraak.example.nl", NOW, WARN) is finding


def test_an_expired_or_not_yet_valid_certificate_is_named_so() -> None:
    expired = ProbeResult(certificate(-1), Trust.TRUSTED)
    future = ProbeResult(certificate(90, since_days=-1), Trust.TRUSTED)

    assert judge(expired, "afspraak.example.nl", NOW, WARN) is Finding.EXPIRED
    assert judge(future, "afspraak.example.nl", NOW, WARN) is Finding.NOT_YET_VALID


@pytest.mark.parametrize(
    ("trust", "finding"),
    [
        (Trust.UNTRUSTED, Finding.UNTRUSTED),
        (Trust.NAME_MISMATCH, Finding.NAME_MISMATCH),
        (Trust.EXPIRED, Finding.EXPIRED),
        (Trust.NOT_YET_VALID, Finding.NOT_YET_VALID),
    ],
)
def test_the_handshake_verdict_wins_over_the_dates(trust: Trust, finding: Finding) -> None:
    # A staging certificate with 89 days left is still untrusted.
    result = ProbeResult(certificate(89), trust)

    assert judge(result, "afspraak.example.nl", NOW, WARN) is finding


def test_a_certificate_for_another_name_is_a_mismatch() -> None:
    result = ProbeResult(certificate(60, "other.example.nl"), Trust.TRUSTED)

    assert judge(result, "afspraak.example.nl", NOW, WARN) is Finding.NAME_MISMATCH


@pytest.mark.parametrize(
    ("name", "host", "covered"),
    [
        ("afspraak.example.nl", "AFSPRAAK.example.nl.", True),
        ("*.example.nl", "afspraak.example.nl", True),
        ("*.example.nl", "deep.afspraak.example.nl", False),
        ("*.example.nl", "example.nl", False),
    ],
)
def test_names_match_case_insensitively_with_one_label_wildcards(
    name: str, host: str, covered: bool
) -> None:
    assert certificate(60, name).covers(host) is covered


def test_no_handshake_is_unreachable() -> None:
    assert judge(ProbeResult(error="timeout"), "x", NOW, WARN) is Finding.UNREACHABLE
    assert judge(ProbeResult(certificate(), None), "x", NOW, WARN) is Finding.UNREACHABLE


def test_days_left_round_down() -> None:
    check = CertificateCheck(NOW, "afspraak.example.nl", Finding.OK, certificate(60))

    assert check.days_left(NOW + timedelta(hours=1)) == 59
    assert check.ok
    assert CertificateCheck(NOW, "x", Finding.UNREACHABLE).days_left(NOW) is None
