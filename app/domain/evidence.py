"""The evidence report: what the application measures about itself, in one document (WP-C2).

An auditor or a client's security officer asks the same questions of every installation: which
version runs, does the identity provider announce what we pin, is the public address and its
certificate in order, which external hosts are configured, where do the logs go, is personal
data removed on time, is a key rotation finished, and which settings come from where. The
application already measures each of these; this module only gives them one shape, so one
command can print them as Markdown for people and as JSON for tools.

The report holds no secret and no personal data by construction: a section is made of short
``name``/``value`` rows the builders in ``app.adapters.evidence`` choose, never of a stored
record. ``host_of`` reduces an address to its host, so a password in an address cannot appear.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
"""Changes when a key of the JSON document is renamed or removed."""


class Status(StrEnum):
    """The verdict of one section."""

    OK = "ok"
    FAILED = "failed"
    WARNING = "warning"
    INFO = "info"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class Row:
    """One fact of a section.

    Attributes:
        name: What it is.
        value: Its value as short text; never a secret.
    """

    name: str
    value: str


@dataclass(frozen=True)
class Section:
    """One topic of the report with its verdict.

    Attributes:
        key: Stable machine name (``oidc``, ``retention``).
        title: Heading in the Markdown report.
        status: The verdict.
        rows: The facts, in order.
    """

    key: str
    title: str
    status: Status
    rows: tuple[Row, ...] = ()


@dataclass(frozen=True)
class Report:
    """The evidence report of one installation at one moment.

    Attributes:
        generated_at: When it was made (UTC).
        sections: The sections, in the order they are printed.
    """

    generated_at: datetime
    sections: tuple[Section, ...]

    @property
    def ok(self) -> bool:
        """Whether no section failed (a warning or a skipped check does not fail the report)."""
        return all(section.status is not Status.FAILED for section in self.sections)

    def to_dict(self) -> dict[str, object]:
        """Return the report as plain data for JSON."""
        return {
            "schema": SCHEMA_VERSION,
            "generated_at": self.generated_at.isoformat(),
            "ok": self.ok,
            "sections": [
                {
                    "key": section.key,
                    "title": section.title,
                    "status": section.status.value,
                    "rows": [{"name": row.name, "value": row.value} for row in section.rows],
                }
                for section in self.sections
            ],
        }


def render_json(report: Report) -> str:
    """Return the report as an indented JSON document."""
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"


def render_markdown(report: Report) -> str:
    """Return the report as Markdown: a heading per section and a table of its rows."""
    lines = [
        "# OpenPlanner evidence report",
        "",
        f"Generated {report.generated_at.isoformat()}. Overall: "
        f"{'no section failed' if report.ok else 'at least one section failed'}. "
        "Secrets appear only as set or not set; no personal data is included.",
        "",
    ]
    for section in report.sections:
        lines += [f"## {section.title}", "", f"Status: {section.status.value}", ""]
        if section.rows:
            lines += ["| Item | Value |", "|---|---|"]
            lines += [f"| {_cell(row.name)} | {_cell(row.value)} |" for row in section.rows]
            lines.append("")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    """Make ``text`` safe inside one Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|") or "-"


def host_of(address: str) -> str:
    """Return ``scheme://host[:port]`` of an address, without credentials, path or query.

    Anything that does not parse as an address with a host yields ``(invalid)``, never the text.
    """
    try:
        parts = urlsplit(address)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "(invalid)"
    if not parts.scheme or not host:
        return "(invalid)"
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")
