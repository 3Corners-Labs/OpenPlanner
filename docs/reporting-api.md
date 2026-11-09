# Usage API

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
| `cut` | Keep one cut column (`booking_kind`, `dispatch_kind`, `subject`, `locale`, `channel`, `outcome`, `actor_class`, `reason`); the other cuts are added up before small cells are left out, and without it every cut is added up. The duration `bucket` is always kept. |
| `format` | `json` (default), `csv` (the columns below) or `ndjson`. |
| `cursor` | The `next_cursor` of the previous page. |

The widest range of one call is `reporting_export_max_days` (at most 400 days), the page size is
`reporting_api_page_size`, and the rate limit per client is `reporting_api_rate_per_minute` calls a
minute; all three are platform settings.

## The answer

Columns of a row: `hour`, `organisation`, `group`, `metric`, `booking_kind`, `dispatch_kind`, `subject`, `locale`, `channel`, `outcome`, `actor_class`, `reason`, `bucket`, `count`, `sum`, `max`. `hour` is the UTC hour (RFC 3339), `count` the number of facts, `sum`
what the metric adds up and `max` the largest value (metric Q9).

`json` returns `{"schema": "openplanner.usage/1", "as_of": …, "next_cursor": …, "rows": […]}`.
`csv` and `ndjson` carry the same rows; `X-Next-Cursor` and `X-As-Of` are headers there (and in
JSON too). Rows are sorted by hour, group, metric and cuts, and a cursor is the key of the last
row sent, so a page does not move while the rollup adds cells. A page that has a `next_cursor` is
full; follow it until it is `null`.

The response has `Cache-Control: private` and an `ETag` that changes when the rollup runs, the
threshold changes or the request changes. Poll with `If-None-Match`: an unchanged answer is `304`.

Errors are `{"error": code}`: `invalid_token` (401), `insufficient_scope` (403),
`group_not_found` (404), `invalid_range` (422: a backwards or too wide range, an unknown metric or
cut, a cursor that is none), `rate_limited` (429).

## Metrics

| ID | Metric |
|---|---|
| U1 | Bookings created |
| U2 | Outcomes |
| U3 | No-show rate |
| U4 | Cancellation rate |
| U5 | Late cancellation |
| U6 | Lead time |
| U7 | Reschedules |
| U8 | Rebooking funnel |
| U9 | Confirmation conversion |
| U10 | Closure impact |
| U11 | Participants per call |
| Q1 | Queue-method mix |
| Q2 | Task wait |
| Q3 | Host punctuality |
| Q4 | Participant wait in the meeting |
| Q5 | Call length |
| Q6 | Abandoned direct calls |
| Q7 | Own-queue claim |
| Q8 | Dispatch health |
| Q9 | Direct-call load |
| C1 | Utilisation |
| C2 | Fill distribution |
| C3 | Idle capacity |
| C4 | Busiest hours and days |
| C5 | Shortfall |
| M1 | Messages |
| M2 | Locale mix |
| T1 | Trial calls |

The definitions, facts and how each adds up are in [reporting.md](reporting.md).

## Examples

    curl -H "Authorization: Bearer $TOKEN" \
      "https://planner.example/api/agent/v1/reports/usage?from=2026-11-02&to=2026-11-08&metric=U2&cut=outcome"

    curl -H "Authorization: Bearer $TOKEN" \
      "https://planner.example/api/agent/v1/reports/usage?from=2026-11-02&to=2026-11-08&format=ndjson"

    curl -H "Authorization: Bearer $TOKEN" -H 'If-None-Match: "<etag>"' "<the same URL>"
