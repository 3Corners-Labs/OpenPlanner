"""Log destinations: validation and the rule which line a destination receives (ADR 0085)."""

from dataclasses import replace

import pytest

from app.domain.audit import Category
from app.domain.errors import DomainError, ErrorCode
from app.domain.log_destinations import (
    DestinationDraft,
    DestinationKind,
    LogDestination,
    accepts,
    categories_text,
    parse_address,
    parse_categories,
    validate_draft,
    validate_secret,
)
from tests.support.domain import NOW


def destination(**changes: object) -> LogDestination:
    values: dict[str, object] = {
        "id": 1,
        "name": "Splunk",
        "kind": DestinationKind.SPLUNK_HEC,
        "address": "https://splunk.example.org:8088/services/collector/event",
        "index": "openplanner",
        "source": "",
        "min_level": "warning",
        "categories": frozenset({Category.AUDIT, Category.SECURITY}),
        "include_diagnostic": False,
        "include_trace": False,
        "enabled": True,
        "secret_set": True,
        "ca_pem": "",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return LogDestination(**values)  # type: ignore[arg-type]


def draft(**changes: object) -> DestinationDraft:
    values: dict[str, object] = {
        "name": "  Splunk  prod ",
        "kind": DestinationKind.SPLUNK_HEC,
        "address": "https://splunk.example.org:8088/services/collector/event",
        "index": "openplanner",
    }
    values.update(changes)
    return DestinationDraft(**values)  # type: ignore[arg-type]


# --- which line a destination receives --------------------------------------------------


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"log.level": "error"}, True),
        ({"log.level": "warning"}, True),
        ({"log.level": "info"}, False),  # below the destination's minimum
        ({"log.level": "trace"}, False),  # trace needs include_trace
        ({"log.level": "bogus"}, False),  # unknown levels rank as info
        ({"log_type": "audit", "event.category": "audit", "log.level": "info"}, True),
        ({"log_type": "audit", "event.category": "security", "log.level": "info"}, True),
        ({"log_type": "audit", "event.category": "system", "log.level": "error"}, False),
        ({"log_type": "audit", "event.category": "diagnostic", "log.level": "error"}, False),
    ],
)
def test_the_filter_follows_level_and_category(record: dict[str, object], expected: bool) -> None:
    assert accepts(destination(), record) is expected


def test_audit_lines_ignore_the_level_as_they_do_on_stdout() -> None:
    sparse = destination(min_level="critical")

    assert accepts(sparse, {"log_type": "audit", "event.category": "audit", "log.level": "info"})


def test_trace_and_diagnostic_are_opt_in() -> None:
    everything = destination(min_level="trace", include_trace=True, include_diagnostic=True)

    assert accepts(everything, {"log.level": "trace"})
    assert accepts(everything, {"log_type": "audit", "event.category": "diagnostic"})
    assert not accepts(replace(everything, include_trace=False), {"log.level": "trace"})
    assert not accepts(
        replace(everything, include_diagnostic=False),
        {"log_type": "audit", "event.category": "diagnostic"},
    )


def test_categories_round_trip_and_drop_unknown_names() -> None:
    text = categories_text({Category.SYSTEM, Category.AUDIT})

    assert text == "audit,system"
    assert parse_categories(text) == {Category.AUDIT, Category.SYSTEM}
    assert parse_categories("audit,diagnostic,nonsense") == {Category.AUDIT}


# --- addresses ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "address"),
    [
        (DestinationKind.SPLUNK_HEC, "https://splunk.example.org:8088/services/collector/event"),
        (DestinationKind.ELASTIC, "https://es.example.org:9200"),
        (DestinationKind.HTTP, "https://collector.example.org/logs"),
        (DestinationKind.SYSLOG, "syslog+tls://syslog.example.org:6514"),
    ],
)
def test_good_addresses_parse_in_production(kind: DestinationKind, address: str) -> None:
    parsed = parse_address(kind, address, production=True)

    assert parsed.host.endswith("example.org")
    assert parsed.secure


@pytest.mark.parametrize(
    ("kind", "address"),
    [
        (DestinationKind.HTTP, ""),
        (DestinationKind.HTTP, "ftp://collector.example.org/logs"),
        (DestinationKind.HTTP, "https:///logs"),
        (DestinationKind.HTTP, "https://user:pw@collector.example.org/logs"),  # no credentials
        (DestinationKind.HTTP, "https://collector.example.org/logs?token=abc"),  # no query
        (DestinationKind.HTTP, "https://collector.example.org/a b"),
        (DestinationKind.HTTP, "syslog+tls://collector.example.org:6514"),  # wrong scheme
        (DestinationKind.SYSLOG, "https://syslog.example.org:6514"),
        (DestinationKind.SYSLOG, "syslog+tls://syslog.example.org"),  # a port is needed
        (DestinationKind.HTTP, "https://collector.example.org:99999/logs"),
    ],
)
def test_bad_addresses_are_refused(kind: DestinationKind, address: str) -> None:
    with pytest.raises(DomainError) as caught:
        parse_address(kind, address, production=False)

    assert caught.value.code is ErrorCode.LOG_DESTINATION_INVALID
    assert caught.value.field == "address"


@pytest.mark.parametrize(
    ("kind", "address"),
    [
        (DestinationKind.HTTP, "http://collector.example.org/logs"),
        (DestinationKind.SYSLOG, "syslog+tcp://syslog.example.org:514"),
        (DestinationKind.SYSLOG, "syslog+udp://syslog.example.org:514"),
    ],
)
def test_unencrypted_addresses_are_for_development_only(
    kind: DestinationKind, address: str
) -> None:
    assert not parse_address(kind, address, production=False).secure
    with pytest.raises(DomainError) as caught:
        parse_address(kind, address, production=True)
    assert caught.value.code is ErrorCode.LOG_DESTINATION_INSECURE


# --- the draft ---------------------------------------------------------------------------


def test_a_draft_is_cleaned() -> None:
    clean = validate_draft(draft(), production=True)

    assert clean.name == "Splunk prod"
    assert clean.source == ""
    assert clean.categories == {Category.AUDIT, Category.SYSTEM, Category.SECURITY}


def test_fields_a_kind_does_not_use_are_emptied() -> None:
    clean = validate_draft(
        draft(
            kind=DestinationKind.HTTP,
            address="https://collector.example.org/logs",
            index="ignored",
            source="ignored",
        ),
        production=True,
    )

    assert (clean.index, clean.source) == ("", "")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": "   "}, "name"),
        ({"name": "x" * 81}, "name"),
        ({"min_level": "loud"}, "min_level"),
        ({"categories": frozenset({Category.DIAGNOSTIC})}, "categories"),
        ({"index": "has space"}, "index"),
        ({"index": "a/b"}, "index"),
        ({"ca_pem": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----"}, "ca_pem"),
        ({"ca_pem": "not a certificate"}, "ca_pem"),
    ],
)
def test_a_bad_draft_names_the_field(changes: dict[str, object], field: str) -> None:
    with pytest.raises(DomainError) as caught:
        validate_draft(draft(**changes), production=True)

    assert caught.value.code is ErrorCode.LOG_DESTINATION_INVALID
    assert caught.value.field == field


def test_a_certificate_authority_may_be_added() -> None:
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"

    assert validate_draft(draft(ca_pem=f"\n{pem}\n"), production=True).ca_pem == pem


def test_secrets_are_trimmed_and_kept_plain() -> None:
    assert validate_secret(DestinationKind.SPLUNK_HEC, " 1234-abcd ") == "1234-abcd"
    for bad in ("", "with space", "x" * 513):
        with pytest.raises(DomainError):
            validate_secret(DestinationKind.SPLUNK_HEC, bad)
    with pytest.raises(DomainError):  # syslog has no credential
        validate_secret(DestinationKind.SYSLOG, "token")
