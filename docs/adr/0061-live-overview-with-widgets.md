# 0061. Live overview with widgets

Status: proposed (2026-10-30), requested by the product owner. Amends ADR 0016 (admin pages without JavaScript) for the overview page only, following the pattern of ADR 0048. Builds on ADR 0047 §5 (overview as status and to-dos), ADR 0014 (matrix) and ADR 0043 (own queue).

## Context

The overview (`/admin/`, `dashboard.view`) lists groups as cards. Admins asked for one page that shows what needs attention now: alerts, the own queue, today's bookings per group, recent configuration changes, running imports and exports, and system status. Different roles need different parts: an agent cares about the queue, an auditor about changes and status. Some of this changes by the minute (queue, alerts), and reloading the page by hand is what ADR 0048 called "waiting for nothing".

## Decision

1. **The overview is a grid of widgets.** Each widget is a server-rendered fragment with its own route under `/admin/overview/w/{widget}` and its own permission from the matrix. A widget the user may not see is not rendered and not reachable.
2. **Widget register.** `app/web/widgets.py` (next to `navigation.py`) declares each widget once: key, i18n title, permission, scope (platform / organisation / group), views it supports (list, table, bar, line, pie), default rows, whether it can be live, and the manual topic. A test fails if a widget has no permission or no `nl`/`en` title.

   | Widget | Permission | Live | Views |
   |---|---|---|---|
   | Alerts | `dashboard.view` | yes | list, bar, pie |
   | Queue across own groups | `queue.work` | yes | table, bar, line |
   | Bookings today per group | `bookings.view` (counts only, no PII) | yes | list, bar, pie |
   | Recent configuration changes | `audit.view` | no | list |
   | Running tasks (imports, exports) | `dashboard.view` | yes | list |
   | System status | `settings.platform.view` | yes | list |
   | My role and reach | `rights.view` | no | list |

3. **Works without JavaScript.** Without the script, the page renders every visible widget once at load with a "Refresh" link. Layout changes (ADR 0062) are plain forms with post/redirect/get.
4. **Live with a small own script**, as in ADR 0048: `static/overview-live.js` polls `GET /admin/overview/live?seen=KEY` and receives `204` or the changed fragments, which it swaps in with `DOMParser`. New alerts are announced in one polite live region (not per widget). Focus is never moved by an update. The page gets `script-src 'self'` in its CSP; every other admin page stays without scripts.
5. **Charts are server-rendered SVG** (no chart library): each has `role="img"`, a text summary with all values, and the same data as a table behind a `<details>`. Colour is never the only signal; bars carry their value as text.
6. **Auto-scroll** of a long list is opt-in per widget, moves one row every few seconds, stops on hover and focus, has a visible pause button (WCAG 2.2.2) and never runs under `prefers-reduced-motion`.
7. **Alerts and banner.** An outage alert in the user's reach shows a banner on every admin page until it is resolved; the user may close it for that alert only. Alert pop-ups follow the user's choice (all, warnings and outages, outages only, off) and stay at least 8 seconds, pausing on hover and focus. Clicking an alert opens the log entry it came from (`system.update` for the system log, otherwise the audit log at that event).
8. **No PII on the overview.** Widgets show counts, group names and times, never booking fields (CLAUDE.md privacy rules). The auditor sees the same widgets without any personal-data permission.
9. **Scope filter.** The top bar may carry an organisation › group filter for the overview and lists. It filters what the user already may see; it never edits and never asks "which organisation?" for a form (ADR 0047 §1).

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `OVERVIEW_LIVE_ENABLED` | C platform | true | on/off | `settings.platform.manage` | Turns polling off for the installation; the page then behaves as §3. |
| `OVERVIEW_POLL_SECONDS` | C platform | 10 | 5–60 | `settings.platform.manage` | Unlike ADR 0048 this page is open all day on many screens, so load is a real concern. |
| `QUEUE_WAIT_WARNING_MINUTES` | D organisation | 8 | 2–60 | `settings.organisation.manage` | Raises a warning alert when the longest wait exceeds it. |
| `QUEUE_WAIT_TARGET_MINUTES` | D organisation | 5 | 1–60 | `settings.organisation.manage` | Shown as the target in the queue widget. |

**Fixed on purpose:** the minimum pop-up time of 8 seconds and the pause rules (WCAG), the single live region, and "no PII on the overview".

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; backlog section 5 | #130 | merged 2026-10-30 |
| 1 | OV-1, OV-2: widget register, fragment routes, overview without script, role defaults | #141 | merged 2026-10-30 |
| 2a | OV-3: the queue, running tasks and system status widgets (alerts wait for OV-6) | #150 | merged 2026-10-30 |
| 2b | OV-4: server-rendered SVG charts (bar, line, pie) with text summary and table | #153 | merged 2026-10-30 |
| 3 | OV-5: `overview-live.js`, live endpoint, CSP on this page only, "poll flood" abuse case | | planned |
| 4 | OV-6, OV-7: alerts, banner, pop-ups, level filter; `QUEUE_WAIT_*` settings | | planned |
| 5 | SH-3: organisation › group filter in the top bar (overview; other lists keep their own filters) | #PR | in PR |

## Consequences

- New: `app/web/widgets.py`, widget routes in `app/admin/routes.py`, `static/overview-live.js` (< 150 lines, no dependency), `admin.css` widget grid and chart classes.
- `docs/admin-map.md` unchanged (same entry); `docs/authorization.md` unchanged (existing permissions).
- Threat model: the live endpoint is a new read path; abuse case "poll flood" with a test against the rate limit, and "widget fragment outside scope".
- e2e: overview with and without script, axe in both themes and locales.
- Manual: topic *Overview* rewritten.
