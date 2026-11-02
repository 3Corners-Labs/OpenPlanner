"""The evidence report's shape: statuses, Markdown, JSON and the reduction of addresses."""

import json
from datetime import UTC, datetime

import pytest

from app.domain.evidence import (
    SCHEMA_VERSION,
    Report,
    Row,
    Section,
    Status,
    host_of,
    render_json,
    render_markdown,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _report(*statuses: Status) -> Report:
    return Report(
        NOW,
        tuple(
            Section(f"s{index}", f"Section {index}", status, (Row("a|b", "x\ny"),))
            for index, status in enumerate(statuses)
        ),
    )


def test_a_failed_section_fails_the_report_but_a_warning_or_a_skipped_check_does_not() -> None:
    assert _report(Status.OK, Status.WARNING, Status.NOT_CHECKED, Status.INFO).ok
    assert not _report(Status.OK, Status.FAILED).ok


def test_the_json_document_carries_schema_verdict_and_every_row() -> None:
    document = json.loads(render_json(_report(Status.OK, Status.FAILED)))

    assert document["schema"] == SCHEMA_VERSION
    assert document["ok"] is False
    assert document["generated_at"] == NOW.isoformat()
    assert [s["status"] for s in document["sections"]] == ["ok", "failed"]
    assert document["sections"][0]["rows"] == [{"name": "a|b", "value": "x\ny"}]


def test_the_markdown_has_a_table_per_section_and_keeps_a_cell_on_one_line() -> None:
    text = render_markdown(_report(Status.OK))

    assert "## Section 0" in text
    assert "Status: ok" in text
    assert "| a\\|b | x y |" in text
    assert "no section failed" in text


def test_a_section_without_rows_has_a_heading_and_no_table() -> None:
    text = render_markdown(Report(NOW, (Section("k", "Empty", Status.INFO),)))

    assert "## Empty" in text
    assert "| Item |" not in text


def test_an_empty_value_is_written_as_a_dash() -> None:
    text = render_markdown(Report(NOW, (Section("k", "T", Status.INFO, (Row("a", " "),)),)))

    assert "| a | - |" in text


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("https://user:zzpass@idp.example.nl:8443/path?q=zzquery#f", "https://idp.example.nl:8443"),
        ("https://idp.example.nl/x", "https://idp.example.nl"),
        ("syslog+tls://logs.example.org:6514", "syslog+tls://logs.example.org:6514"),
        ("https://[2001:db8::1]:8443/x", "https://[2001:db8::1]:8443"),
        ("not an address", "(invalid)"),
        ("https://", "(invalid)"),
        ("https://host:notaport/", "(invalid)"),
        ("", "(invalid)"),
    ],
)
def test_an_address_is_reduced_to_scheme_host_and_port(address: str, expected: str) -> None:
    assert host_of(address) == expected
