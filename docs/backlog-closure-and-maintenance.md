# Backlog: closure page and maintenance mode

Build plan for ADR 0037 (closure page with scenarios, contact options and an image) and
ADR 0038 (maintenance mode). Sizes: S ≈ up to 2 days, M ≈ up to a week, L ≈ more than a week.
Each item is one PR with the usual gates (CLAUDE.md); "done" always includes tests first
(domain, then adapter, then HTTP), nl/en texts where users see something, the manual page of
the screen (ADR 0033), and the docs named in the item.

Order: the contact block comes first because both decisions use it. After that the two
streams are independent and can run in parallel. Migration and ADR numbers are taken from
`origin/main` at the moment a PR opens, not from this plan.

```
CB-1 ─┬─ CL-1 ─ CL-2 ─ CL-3 ─ CL-4 ─ CL-5
      └─ MM-1 ─ MM-2 ─ MM-3 ─ MM-4 ─ MM-5
```

## 0. Shared: contact block — ADR 0037 §1–2

| ID | Item | Size | Done when |
|---|---|---|---|
| CB-1 | Domain `ContactOption` (kinds phone, email, website, chat, address) and `ContactBlock` with validation; table for contact blocks; organisation default on the organisation page (`organisation.closure`); template partial `_contact_block.html` | M | Validation tests per kind (`https` only, refused `javascript:`/`data:`/`http:`, phone characters, length limits, at most six); matrix test for the card; audited with counts only; partial in `e2e/pages.json` via the organisation page |

## 1. Closure page — ADR 0037

| ID | Item | Size | Done when |
|---|---|---|---|
| CL-1 | Closure content: heading per locale and a copy of the contact block on the closure; group override (`closure.manage`) falling back to the organisation default | M | Reversible migration (downgrade keeps the notices); domain tests for the fallback; existing closure tests unchanged and green |
| CL-2 | Citizen pages: the active closure replaces the calendar with the closure page; the upcoming banner links to it; the management page of a suspended booking shows the block | M | `e2e/pages.json`: closed group, upcoming closure, suspended booking, in nl and en; keyboard flow; axe zero violations; Lighthouse ≥ 95 |
| CL-3 ✅ | Closure image: upload (PNG/JPEG/WebP by magic bytes, ≤ 500 kB), alt text nl/en or "decorative", served from our origin with `nosniff`; metadata stripped without a new dependency (or an ADR if one is needed); only images of visible closures public; logo validation extracted to one place (second use) | M | Abuse-case tests: HTML or SVG renamed to `.png`, too large (413 unread), missing alt text, EXIF location gone after upload, scenario image not public; CSP unchanged (`img-src 'self'`) |
| CL-4 | Scenarios: organisation scenarios (`organisation.closure`) and group scenarios (`closure.manage`); "start closure" picks a scenario and copies its content; organisation-wide closure uses one organisation scenario | L | Tests: edit after start does not change the closure; group admin cannot edit an organisation scenario (matrix); audit `closure.start,scenario:N,bookings:N` without texts |
| CL-5 | Emails: notice and contact options as a fixed block in the HTML and plain-text parts of the suspension and update mails, with a link to the closure page; docs: threat model (new abuse cases), dataflow (contact options are organisation data), runbook and manual (preparing scenarios, examples) | M | Mail snapshot tests in nl and en; plain-text part contains the contact options; threat-model rows each name their test |

## 2. Maintenance mode — ADR 0038

| ID | Item | Size | Done when |
|---|---|---|---|
| MM-1 | State: table `maintenance_state`, setting `MAINTENANCE_MODE`, domain service (on, off, expected end, reason code), permission `system.maintenance` (global, superuser); page `/admin/maintenance` with fresh login and same-origin POST; CLI `maintenance on/off/status` | M | Reversible migration; `docs/authorization.md` regenerated; `.env.example` updated; audit `maintenance.on`/`maintenance.off`; the environment override cannot be switched off in the portal (test) |
| MM-2 | Enforcement: `MAINTENANCE_WRITABLE` in the matrix, `maintenance_safe=True` on the integration check only, refusal in `RequirePermission` for every other non-GET request; audit `maintenance.refused`; disabled buttons with the reason, from the matrix; admin maintenance bar (shared with the environment bar) | M | Matrix test: every admin endpoint × role with maintenance on (only the allowed ones pass, and only for the superuser); 5-second cache tested with the fake clock |
| MM-3 | Citizen and machine API: middleware rules for the citizen POSTs (book, reschedule, cancel, direct call) and machine-API writes → `503` with `Retry-After` / code `maintenance`; maintenance page with branding, expected end and the organisation's contact block; webhooks, join pages, health and the `purge_pii` jobs untouched | M | Tests per route group; schemathesis green with the `503` documented in OpenAPI; `e2e/pages.json`: maintenance page with and without contact block, nl and en; `/readyz` stays 200 |
| MM-4 | Updater: `maintenance on` before the backup, `off` after healthy, rollback or restore; `failed` leaves it on | S | Updater tests (shellcheck and the scripted cases of #36) cover all four outcomes |
| MM-5 | Alerting and docs: system event when maintenance is on longer than 4 hours; runbook section "Onderhoudsmodus"; threat model (organisation admin writing, citizen POST past the middleware, machine-API write); architecture module map and diagrams | S | Each threat-model row names its test; runbook reviewed by the operator |

## Not in scope

- Automatic end of maintenance at the expected time (ADR 0038 §6).
- Images in emails (ADR 0037 §5).
- Using the contact block for the "queue full" message of direct calls (ADR 0026); possible later
  without a new decision.
