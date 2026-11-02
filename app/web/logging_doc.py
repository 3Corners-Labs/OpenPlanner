"""``docs/logging.md``, generated from the register of actions (ADR 0103 point 2).

``python -m app.cli logging-doc > docs/logging.md`` writes it; a test fails when the committed
file differs, as for ``docs/settings.md``. The prose around the tables lives here; the tables
of trace points and actions come from ``app.domain.log_events``.
"""

from collections.abc import Iterable

from app.domain.log_events import BY_OUTCOME, ENTRIES, FAMILIES, LogCategory, LogEvent

_INTRO = """# Logging: levels, the record, the actions and where lines go

Generated from `app/domain/log_events.py` by
`python -m app.cli logging-doc > docs/logging.md`; do not edit by hand. CI fails when this
file differs from the code.

What OpenPlanner writes to its log, at which level, in which shape, and how it leaves the
installation. Decisions: ADR 0040 (one event schema, levels), ADR 0034 (audit events on the
log stream), ADR 0042 §6 (`LOG_LEVEL` with expiry), ADR 0085 (the `TRACE` level, the trace
points and the log destinations in the portal), ADR 0103 (the register of actions).

## The record

Every line is one JSON object on stdout in the `openplanner.event/2` envelope (ADR 0040,
`app/logging.py`): `@timestamp` (RFC 3339 UTC, milliseconds), `log.level`, `message`,
`service.name` (`web` or `worker`), `service.version`, `installation` (`INSTALLATION_NAME`)
and, inside a request or job, `trace.id`, `organisation` and `group`. Every line passes the
PII mask: the fields of `app/domain/privacy.py` are replaced by `***` at any depth, as a
safety net on top of the rule that code never passes personal data to a logger (semgrep,
`.semgrep/rules.yml`). The access log of uvicorn is off (paths carry management tokens) and
the `http.request` and `outbound.request` trace points carry what is safe instead of the
request lines of httpx (full URL) and the statement lines of the SQL driver (bound values).
Libraries never log below `warning` (see *Levels*).

Audit, system and security events are on the same stream after their transaction commits,
marked `log_type: "audit"` (`docs/audit-events.md`). Diagnostic events (ADR 0040 part 4) are
in the portal only.

## Levels

| Level | Numeric | What |
|---|---|---|
| `critical` | 50 | the process cannot go on |
| `error` | 40 | a job or request failed |
| `warning` | 30 | retried, denied, degraded |
| `info` | 20 | the normal trail: jobs done, settings changed, start-up |
| `debug` | 10 | detail for troubleshooting |
| `trace` | 5 | the fixed trace points below, one line each (ADR 0085) |

`LOG_LEVEL` (class C, *Advanced settings* or `settings set LOG_LEVEL ... --hours N`) is the
minimum. Set in the portal or from the host it falls back to the default after at most 24
hours; `TRACE` after at most `TRACE_LEVEL_MAX_HOURS` (default 4), because the volume is higher
and the lines closer to the data. The audit line bypasses the level, so a quiet log never
drops the trail.

`LOG_LEVEL` governs our own loggers only: `openplanner.*` (including the trace logger),
the structlog loggers of the app and `uvicorn.error`. Every other logger (`httpcore`,
`aiosqlite`, `sqlalchemy`, a library added later) lets nothing below `warning` through, at any
`LOG_LEVEL`, after a live level change, and even if the library sets its own logger to
`DEBUG`: libraries log at DEBUG what they handle (bound SQL values, response headers), and a
line can leave the host through log shipping (ASVS V16.2.5). It is a fixed protection, not a
setting (ADR 0042: a protection is never switched off in C or D). An allowlist, so a new
library is covered without a change here. Tests: `tests/test_logging.py`.

"""

_OUTRO = """## Where the lines go

- **stdout, always.** `docker compose logs -f web worker`; the `json-file` driver rotates at
  5 files of 20 MB per container (ADR 0040 part 1).
- **The host route** (ADR 0034): Docker's `splunk` logging driver, a Splunk Universal
  Forwarder or Fluent Bit reading the rotated files; `docs/runbook.md`, *Shipping audit events
  to a log platform*. Route `log_type = audit` to its own index.
- **Log destinations** (ADR 0085 part 1): syslog (RFC 5424 over TLS, TCP or UDP), Splunk HEC,
  Elastic or OpenSearch (`_bulk` into a data stream) and NDJSON over HTTP, each a copy of the
  masked lines stdout carries, with its own minimum level, the audit categories it takes and an
  opt-in for trace lines and diagnostic events. Every process (web, worker) keeps one bounded
  queue per destination and delivers in batches in the background; a full queue drops the
  oldest line, a failing platform is retried with backoff, and a batch the platform will never
  accept is dropped instead of blocking the rest. A destination narrows the process's own
  level and never widens it: it sees trace lines only while `LOG_LEVEL` is `TRACE` too. The
  superuser manages them under Platform → Log destinations (`docs/manual/en/logging.md`):
  add, send a test line, switch on, change and delete; the auditor sees the list and the
  status of each process.

## Settings

| Key | Class | Default | Bounds |
|---|---|---|---|
| `LOG_LEVEL` | C | `INFO` | `TRACE`–`CRITICAL`, expires |
| `TRACE_LEVEL_MAX_HOURS` | C | 4 | 1–24 |
| `SQL_SLOW_MS` | C | 200 | 10–10 000 |
| `INSTALLATION_NAME` | B | empty | `[A-Za-z0-9_.-]{0,64}` |

The full list with every class is in `docs/settings.md`.
"""

_HEADINGS = {
    LogCategory.SECURITY: "Security",
    LogCategory.AUTHZ: "Authorisation decisions",
    LogCategory.AUDIT: "Admin actions (audit)",
    LogCategory.SYSTEM: "System events",
    LogCategory.OPERATIONS: "Operations",
    LogCategory.DIAGNOSTIC: "Diagnostic",
}
"""Section per category, in the order a reader looks for trouble."""

_ACTIONS = """## Actions

Every action the application logs or audits is registered once in
`app/domain/log_events.py` with its category, level, fields, an English message, what it
means and what to do. A log call that names an unregistered action, passes an undeclared
field or logs at an unlisted level fails `tests/test_log_events_scan.py`; an audited action
that is not registered fails the test run (`tests/conftest.py`). *Level* `outcome` means the
level follows the event's outcome: success and skipped `info`, retry and denied `warning`,
failure `error`. The fields in braces fill the message; `actor`, `organisation`, `group`,
`booking_id`, `outcome`, `reason` and `detail` are on every event that has them.

"""

_FAMILIES = """### Generated

Three kinds of action are generated, not listed:

- **a permission** (`group.edit`, `staff.manage`, ...): an admin used, or was denied, that
  right; level `outcome`, category `audit`. `docs/authorization.md` says who holds each one.
- **a machine API scope** ({scopes}): a client of the machine API used, or was denied, that
  scope (ADR 0023).
- **a worker job**, `job.<type>`: the outcome of one job, shown on the booking's timeline;
  the types are {jobs}.

"""


def _code(values: Iterable[str]) -> str:
    """Return the values as comma-separated inline code, or a dash."""
    return ", ".join(f"`{v}`" for v in values) or "–"


def _levels(entry: LogEvent) -> str:
    """Return the levels of an entry for a table cell."""
    return ", ".join("outcome" if level == BY_OUTCOME else f"`{level}`" for level in entry.levels)


def _trace_points() -> str:
    """Return the section on trace points."""
    rows = [
        "## Trace points",
        "",
        "The only sources of `trace` lines are the points below, written through",
        "`app.logging.tracer.trace(point, **fields)`. A point takes its registered fields and",
        "nothing else: the `Tracer` refuses any other field, and a test feeds personal data into",
        "every field and asserts the stream is clean. Adding a trace point is a change to the",
        "register and the PII test together.",
        "",
        "| Point | Fields | Where | Never |",
        "|---|---|---|---|",
    ]
    for entry in ENTRIES:
        if entry.is_trace_point:
            rows.append(
                f"| `{entry.action}` | {_code(entry.fields)} | {entry.meaning} | {entry.never} |"
            )
    return "\n".join(rows) + "\n\n"


def _category(category: LogCategory) -> str:
    """Return the table of the listed actions of one category."""
    rows = [
        f"### {_HEADINGS[category]}",
        "",
        "| Action | Level | Fields | Message | Meaning | What to do |",
        "|---|---|---|---|---|---|",
    ]
    for entry in sorted(ENTRIES, key=lambda e: e.action):
        if entry.category is category and not entry.is_trace_point:
            rows.append(
                f"| `{entry.action}` | {_levels(entry)} | {_code(entry.fields)} | "
                f"{entry.message} | {entry.meaning} | {entry.todo} |"
            )
    return "\n".join(rows) + "\n\n"


def render_markdown() -> str:
    """Return ``docs/logging.md``: the prose, the trace points and every action."""
    scopes = [e.action for e in FAMILIES if e.category is LogCategory.AUDIT and ":" in e.action]
    jobs = [e.action.removeprefix("job.") for e in FAMILIES if e.action.startswith("job.")]
    sections = [_INTRO, _trace_points(), _ACTIONS]
    sections += [
        _category(category) for category in _HEADINGS if category is not LogCategory.DIAGNOSTIC
    ]
    sections.append(_FAMILIES.format(scopes=_code(scopes), jobs=_code(jobs)))
    return "".join(sections) + _OUTRO
