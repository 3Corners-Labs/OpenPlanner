"""``docs/reporting.md``, generated from the metric catalogue (ADR 0117 point 6).

``python -m app.cli reporting-doc > docs/reporting.md`` writes it; a test fails when the committed
file differs, so the published definitions are the ones the code uses, as for
``docs/settings.md``.
"""

from app.domain.call_facts import FAILURE_CODES, LEAVE_REASONS
from app.domain.message_usage import PURPOSES
from app.domain.privacy import USAGE_FIELDS
from app.domain.slot_close import OUTCOME_RULE
from app.domain.usage import BUCKET_LABELS, FACT_HOUR, METRIC_VERSION, METRICS
from app.domain.usage_report_csv import (
    CUT_FIELDS,
    HIDDEN,
    SCREEN_HEADER,
    TIDY_HEADER,
    TIDY_VERSION,
)
from app.domain.usage_rollup import FILL_CLASSES, feed_rows
from app.domain.usage_suppress import MIN_CELL_SIZE_FLOOR

_INTRO = f"""# Reporting on use

Generated from `app/domain/usage.py` and `app/domain/privacy.py` by
`python -m app.cli reporting-doc > docs/reporting.md`; do not edit by hand. CI fails when this
file differs from the code. The decision is
[ADR 0117](adr/0117-reporting-on-use-from-a-store-that-holds-no-personal-data.md).

The statistics hold no personal data. They are counts and sums per hour and per cut, never one
line per booking, and every read hides a cell below the threshold (never below
{MIN_CELL_SIZE_FLOOR}). Metric definitions are version {METRIC_VERSION}; a change of a definition
or a bucket bound needs a new version.
"""


def render_markdown() -> str:
    """Return ``docs/reporting.md``: metrics, facts and their hour, buckets, the allow-list."""
    parts = [_INTRO, "\n## Metrics\n\n"]
    parts.append("| ID | Metric | Definition | Facts | Adds up as |\n|---|---|---|---|---|\n")
    for m in METRICS:
        facts = ", ".join(f"`{kind.value}`" for kind in m.facts)
        team = " (team view, off by default)" if m.staff else ""
        parts.append(
            f"| {m.id} | {m.name}{team} | {m.definition} | {facts} | {m.aggregation.value} |\n"
        )
    parts.append("\n## Facts and the hour they are keyed on\n\n")
    parts.append("| Fact | Hour from |\n|---|---|\n")
    parts.extend(f"| `{kind.value}` | {source.value} |\n" for kind, source in FACT_HOUR.items())
    parts.append("\n## How a fact becomes a cell\n\n")
    parts.append(
        "One row per metric a fact kind feeds. The count of a cell is always the number of facts; "
        "the figure is what the metric adds up. A word in `reason_code` is the metric's own "
        "state, not the fact's reason.\n\n"
    )
    parts.append("| Fact | Metric | Figure | Cut by | Only when |\n|---|---|---|---|---|\n")
    parts.extend(
        f"| `{r.kind.value}` | {r.metric} | {r.figure} | {r.cuts} | {r.condition} |\n"
        for r in feed_rows()
    )
    parts.append("\n## Closed lists\n\n")
    parts.append(
        "A `reason_code` of a fact is always one of these; a free text never becomes one.\n\n"
    )
    parts.append(
        f"- Failure codes (`call.failed` is `task.`, `meeting.` or `integration.` plus one of): "
        f"{', '.join(f'`{c}`' for c in sorted(FAILURE_CODES))}\n"
    )
    left = ", ".join(f"`{c}`" for c in sorted(LEAVE_REASONS))
    parts.append(f"- Why a participant left a direct call: {left}\n")
    purposes = ", ".join(f"`{c}`" for c in sorted(set(PURPOSES.values())))
    parts.append(f"- Purpose of a message: {purposes}\n")
    parts.append("- Shortfall of a slot: `open`, `accepted`, `resolved`\n")
    parts.append(f"- Fill of a closed slot: {', '.join(f'`{c}`' for c in FILL_CLASSES)}\n")
    parts.append("\n## How an outcome is derived\n\n")
    parts.append(OUTCOME_RULE.strip() + "\n")
    parts.append("\n## Duration buckets\n\n")
    parts.append(
        "The same ten buckets for every duration; a signed duration (a host who was early) "
        "uses the mirrored set.\n\n"
    )
    parts.extend(f"{code}. {label}\n" for code, label in enumerate(BUCKET_LABELS))
    parts.append("\n## What a fact may hold\n\n")
    parts.append(
        "Exactly these fields and no others; `staff_id` only on the separate team-member fact.\n\n"
    )
    parts.extend(f"- `{field}`\n" for field in USAGE_FIELDS)
    parts.append(_export())
    return "".join(parts)


def _export() -> str:
    """Return the section on the two CSV files and the columns of the tidy one."""
    screen = ", ".join(f"`{c}`" for c in SCREEN_HEADER)
    tidy = ", ".join(f"`{c}`" for c in TIDY_HEADER)
    return f"""
## CSV export

Both files are UTF-8 with a byte order mark, `;` as separator, RFC 4180 quoting and CRLF line
ends; a text cell that starts like a formula gets a leading `'`. The response carries
`X-Content-SHA256` (the checksum of the file) and the audit event `reports.export` keeps it, with
the family, the range, the row count and who; never a figure. An export over
`reporting_export_max_rows` rows or `reporting_export_max_days` days is refused, never cut short.
Holders of `reports.export` download from the Usage page, for the groups of their scope.

**Screen CSV** (`?view=volume`, `queue`, `capacity` or `channels`): exactly the tables of that
page, after the small-cell rule, in the screen's order. Columns: {screen}. `table` is the name of
the table, `row` and `column` are codes (a day, an outcome, a bucket number, a weekday 0 to 6 with
Monday first, an hour), `value` is the count, percent or seconds, `note` is `{HIDDEN}` where the
screen shows `< n` or no figure, and the value is then empty. A total has the row or column
`total`. The file holds no interface text, so it does not change with the language.

**Tidy CSV `{TIDY_VERSION}`** (`?view=tidy`): one row per hourly cell, for analysts. Columns:
{tidy}. `hour` is the UTC hour (RFC 3339), `organisation` and `group` are slugs, `metric` is an ID
of the table above, the cut columns hold the codes of the cell (empty when the metric is not cut
by it), `bucket` is the duration bucket number, `count` is the number of facts, `sum` is what the
metric adds up and `max` is the largest value (Q9's peak). A cell below the threshold is not in the
file at all. The range is whole group-local days. The column list and its meaning change only with
a new version, which needs an amendment of the ADR.
"""


def render_api_markdown() -> str:
    """Return ``docs/reporting-api.md``: the call, its parameters, metrics and examples."""
    metrics = "".join(f"| {m.id} | {m.name} |\n" for m in METRICS if not m.staff)
    cuts = ", ".join(f"`{c}`" for c in CUT_FIELDS)
    columns = ", ".join(f"`{c}`" for c in TIDY_HEADER)
    cut_row = (
        f"| `cut` | Keep one cut column ({cuts}); the other cuts are added up before small cells "
        "are left out, and without it every cut is added up. The duration `bucket` is "
        "always kept. |"
    )
    return f"""# Usage API

Generated from the metric catalogue and the row layout by
`python -m app.cli reporting-api-doc > docs/reporting-api.md`; do not edit by hand. CI fails when
this file differs from the code. The decision is
[ADR 0117](adr/0117-reporting-on-use-from-a-store-that-holds-no-personal-data.md) point 13; the
machine API in general is in [agent-api.md](agent-api.md).

`GET /api/agent/v1/reports/usage` returns the hourly usage figures of the groups an API client may
read: counts and sums per hour, group, metric and cut, never a line per booking and never personal
data. The client holds the scope `reports:read`, granted by the superuser per client like the
other scopes, within its organisation and the groups it is narrowed to. A cell under the
installation's small-count threshold is not returned, so a client sees what the screens show.

## Parameters

| Parameter | Meaning |
|---|---|
| `from`, `to` | First and last day, inclusive, whole group-local days. Required. |
| `metric` | One metric id of the table below. Without it, every metric. |
| `group` | One group slug of the client. A group outside the grant is `group_not_found`. |
{cut_row}
| `format` | `json` (default), `csv` (the columns below) or `ndjson`. |
| `cursor` | The `next_cursor` of the previous page. |

The widest range of one call is `reporting_export_max_days` (at most 400 days), the page size is
`reporting_api_page_size`, and the rate limit per client is `reporting_api_rate_per_minute` calls a
minute; all three are platform settings.

## The answer

Columns of a row: {columns}. `hour` is the UTC hour (RFC 3339), `count` the number of facts, `sum`
what the metric adds up and `max` the largest value (metric Q9).

`json` returns `{{"schema": "openplanner.usage/1", "as_of": …, "next_cursor": …, "rows": […]}}`.
`csv` and `ndjson` carry the same rows; `X-Next-Cursor` and `X-As-Of` are headers there (and in
JSON too). Rows are sorted by hour, group, metric and cuts, and a cursor is the key of the last
row sent, so a page does not move while the rollup adds cells. A page that has a `next_cursor` is
full; follow it until it is `null`.

The response has `Cache-Control: private` and an `ETag` that changes when the rollup runs, the
threshold changes or the request changes. Poll with `If-None-Match`: an unchanged answer is `304`.

Errors are `{{"error": code}}`: `invalid_token` (401), `insufficient_scope` (403),
`group_not_found` (404), `invalid_range` (422: a backwards or too wide range, an unknown metric or
cut, a cursor that is none), `rate_limited` (429).

## Metrics

| ID | Metric |
|---|---|
{metrics}
The definitions, facts and how each adds up are in [reporting.md](reporting.md).

## Examples

    curl -H "Authorization: Bearer $TOKEN" \\
      "https://planner.example/api/agent/v1/reports/usage?from=2026-11-02&to=2026-11-08&metric=U2&cut=outcome"

    curl -H "Authorization: Bearer $TOKEN" \\
      "https://planner.example/api/agent/v1/reports/usage?from=2026-11-02&to=2026-11-08&format=ndjson"

    curl -H "Authorization: Bearer $TOKEN" -H 'If-None-Match: "<etag>"' "<the same URL>"
"""
