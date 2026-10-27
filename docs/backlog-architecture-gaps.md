# Backlog: architecture gaps

Four gaps an architecture review of October 2026 found, each with its decision record and the
work items that close it. Sizes: S ≈ up to 2 days, M ≈ up to a week, L ≈ more than a week.
Each item is one PR with the usual gates (CLAUDE.md); "done" always includes tests, nl/en texts
where users see something, and the docs named in the item.

Suggested order: audit export (small, asked for by security teams), scaling seams (small,
answers the review question), contact-center neutrality (needed before a second platform),
embedding (largest).

## 1. Provider-neutral contact center — ADR 0032 (formerly 0021)

The design is complete in the proposed ADR; what remains is the decision and the build.

| ID | Item | Size | Done when |
|---|---|---|---|
| CC-0 | Product owner accepts the ADR and names the first platform after WxCC (new open point) | S | ADR status `accepted`; open point in HANDOVER §11 |
| CC-1 | Domain and schema: `ContactCenterKind`, `ContactCenterRouting`, table `group_contact_center`; `wxcc_*` group columns, `Booking.wxcc_task_id` and job type `dispatch_wxcc_task` migrated to neutral names | M | Reversible migration tested up/down with data; no `wxcc` left in `app/domain/` except the `webex_cc` kind |
| CC-2 | Port and registry: `ContactCenterTask`, `dict[ContactCenterKind, ContactCenterDispatcher]`; WxCC adapter maps the neutral task to Create Task v2 | M | Recorded-payload test per adapter asserts first name and host link only |
| CC-3 | Webhooks at `/webhooks/contact-center/{kind}`, verified by the adapter within 300 s; `/webhooks/wxcc` kept as alias | S | Signature and replay tests per kind; runbook step to re-register |
| CC-4 | Admin: one contact-center connection per organisation (`integrations.manage`); group form rendered from the adapter's `ProviderDescriptor` | M | No `if kind ==` in routes or templates; authorisation-matrix tests unchanged and green |
| CC-5 | Docs: architecture, dataflow, webhooks, threat model, `scripts/diagrams/content.py` | S | Plates regenerated; data flow says "contact-center provider" |
| CC-6 | Second platform adapter (for example Genesys Cloud), with its own ADR for API, auth and webhooks | L | Contract tests on recorded fixtures; no change outside its adapter and descriptor |

## 2. Audit events to an external log platform — ADR 0034

| ID | Item | Size | Done when |
|---|---|---|---|
| AU-1 | Post-commit emit of every audit event as `log_type: "audit"`, schema `openplanner.audit/1`, in the audit adapter | S | Tests: once after commit, never after rollback, only schema fields, PII masked |
| AU-2 | Setting `INSTALLATION_NAME`; schema documented in `docs/audit-events.md` | S | `.env.example` and settings docs updated |
| AU-3 | Runbook: shipping with Docker's `splunk` logging driver, Universal Forwarder or Fluent Bit; `docs/dataflow.md` names the log platform as recipient of admin subjects | S | Runbook section reviewed by the client's security team |
| AU-4 | Webhook rejections and Webex token-refresh failures as deduplicated global system events (left open by ADR 0031) | M | Flood of invalid webhooks gives one event per window, test included |

## 3. Embedding on a host website — ADR 0035 (WP3c)

| ID | Item | Size | Done when |
|---|---|---|---|
| EM-1 | Allowed origins per organisation: table, migration, admin card (`integrations.manage`), audited changes | M | Exact-origin validation tests; matrix and generated `docs/authorization.md` updated |
| EM-2 | Widget API `/api/v1/widget/{org}/{group}/`: month availability, day times, create booking; CORS for allowed origins only; `require_allowed_origin`; same rate limits and honeypot | M | OpenAPI diff reviewed; schemathesis green; abuse-case tests (foreign origin, slot exhaustion, no token or PII in responses) |
| EM-3 | Web Component `<gov-booking>` under `widget/`, esbuild in CI, tokens and `::part()`, i18n, ARIA inside the shadow root; versioned script with SRI hash | L | Works without host CSS; keyboard-only flow; no inline styles, no `!important` |
| EM-4 | Test host page in `e2e/pages.json` for every widget state | M | axe zero violations, Lighthouse ≥ 95, keyboard flow, nl and en |
| EM-5 | Docs: `docs/widget.md` (attributes, tokens, parts, host CSP, origin registration), threat model flow, dataflow | S | A host developer can embed from the doc alone |

## 4. Scaling beyond one VM — ADR 0036

| ID | Item | Size | Done when |
|---|---|---|---|
| SC-1 | Rate-limit storage as a setting (`RATE_LIMIT_STORAGE_URI`, default `memory://`) | S | Test with two limiter instances on one shared storage |
| SC-2 | Contract test for the write lock: concurrent bookings of the last place never both succeed, against the SQL unit of work and the fake | S | Test names the guarantee, not SQLite |
| SC-3 | Backup and restore behind one interface in `app/adapters/db/backup.py` | S | Updater (ADR 0029) unchanged in behaviour; its tests green |
| SC-4 | Measurements: booking transaction duration, lock wait, busy-timeout count in the logs (no PII) | S | Visible in the log platform next to AU-1 |
| SC-5 | Runbook: the triggers of ADR 0036 and where to read the measurements | S | Operations team knows when to raise the scaling ADR |
| SC-6 | Only when a trigger fires: ADR for Postgres and multiple `web` replicas, then the adapter work | L | Separate decision; not planned |
