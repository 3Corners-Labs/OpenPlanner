# 0038. Maintenance mode: the whole installation, data frozen, integrations open

Status: accepted (2026-10-27), decided by the product owner. Builds on ADR 0014 (authorisation
matrix), ADR 0029 (updates from the portal) and ADR 0037 (contact block).
The "read-only" wording of the group check (§3: "writes nothing but its report") is amended
by ADR 0065 §4. §6's fixed 4-hour alert is now the settings `maintenance_alert_hours` (default 4) and
`maintenance_alert_repeat_hours` (default 24) in `app/config.py`, read by `app/jobs/worker.py`.

## Context

An emergency closure (ADR 0024, ADR 0037) is a business decision about one group or one
organisation: the team cannot take appointments. Maintenance is a technical state of the whole
installation: an operator is working on the platform and data written in the meantime may be
lost or may not reach anyone. They need different flows, and the product owner asked what
should still be possible in production while maintaining.

Situations that call for it:

- An update or a restore through the host updater (ADR 0029). A rollback puts back a backup
  taken minutes earlier, so bookings made in between would disappear.
- Repairing an integration that fails: SMTP credentials expired, a rotated WxCC or SMS key,
  webhook registrations to renew. Meanwhile citizens should not book appointments whose
  confirmation or work item will not arrive.
- Investigating an incident with data that must not change underneath.

In each case the work happens on **integrations and the platform**, not on groups, schedules or
users: that is ordinary admin work and needs no maintenance window. Appointments that are
already confirmed take place regardless; maintenance must not leave an agent or a citizen
waiting in an empty call.

## Decision

1. **One switch for the whole installation.** Maintenance applies to every organisation at once.
   It is on when either of these says so:
   - the `maintenance_state` row (on/off, since, by whom, expected end, optional reason code),
     switched in the portal on `/admin/maintenance` with the new permission
     `system.maintenance` (global level, superuser only, fresh login, same-origin POST, audited);
   - the environment variable `MAINTENANCE_MODE=true`, for work where the database itself is
     unavailable or being restored. The portal shows that it comes from the environment and
     cannot be switched off there.

   The `web` process reads the row with a cache of at most 5 seconds.

2. **What stays open and what is frozen.**

   | Area | During maintenance |
   |---|---|
   | Citizen: book, reschedule, cancel, start a direct call | closed: `503` with `Retry-After` for POSTs, the maintenance page instead of the form |
   | Citizen: view an existing appointment, join page of a confirmed call | open, read-only |
   | Machine API (ADR 0023): reads | open |
   | Machine API: writes | `503` with code `maintenance` |
   | Confirmed appointments: reminders, work items, meetings (worker) | continue |
   | Privacy: `purge_pii` jobs and the nightly sweep (worker) | continue, never paused |
   | Inbound webhooks from Webex and WxCC | open (they record what happens in running calls) |
   | Admin: every page | readable |
   | Admin: changes to organisations, groups, schedules, texts, closures, bookings, users | refused with "Onderhoudsmodus: alleen lezen" |
   | Admin: integrations (SMTP, SMS, WxCC/Webex, webhook registrations), "Koppeling controleren", sending a test mail | open, **superuser only** |
   | Admin: updates (ADR 0029), the maintenance switch | open, superuser only |
   | Health: `/healthz`, `/readyz` | unchanged, so the updater's health checks keep working |

3. **Enforced in one place, from the matrix.** The authorisation matrix marks which
   permissions may write during maintenance (`MAINTENANCE_WRITABLE`: `integrations.manage`,
   `system.update`, `system.maintenance`); the test mail falls under `integrations.manage`. A
   route that writes for another permission can opt in with `maintenance_safe=True`; the only one
   is "Koppeling controleren" (`group.edit`), which writes nothing but its report. During
   maintenance both are allowed for the superuser only. `RequirePermission` refuses every
   other non-GET request while maintenance is on, and every refusal is logged and audited as
   `maintenance.refused` with the permission, never the form content. A fixed list of
   middleware rules handles the citizen POST routes and the machine API, so no route can forget
   it. Superuser is not an exception: during maintenance the superuser too can change only what
   the table allows.

4. **What people see.**
   - **Citizens:** a maintenance page in the organisation's branding: a fixed text from the
     catalogue ("We voeren onderhoud uit"), the expected end when given, and the organisation's
     contact block (ADR 0037 §2). Without a contact block the page says to try again later.
   - **Admins:** a bar on every admin page, built like the environment bar, saying maintenance is
     on, since when, and the expected end. Buttons that would be refused are shown disabled with
     the reason, from the same matrix.

5. **The host updater uses it.** Before taking its backup, the updater switches maintenance on
   (`python -m app.cli maintenance on --reason update`), and after a healthy result, a rollback
   or a restore it switches it off again. The backup is taken with maintenance already on, so a
   restored backup also has it on and the updater's final step switches it off. An update that
   ends as `failed` leaves maintenance on, so citizens see the maintenance page instead of a
   half-working system.

6. **No automatic end.** The expected end is information only; maintenance ends when a
   superuser (or the updater) switches it off. The daily alerting reports maintenance that has been on
   longer than 4 hours as a system event (ADR 0031).

## Consequences

- During maintenance no booking is lost by a rollback, and no confirmation goes out through a
  broken integration, while running and planned calls continue.
- Inbound webhooks stay open, so a call event that arrives between the updater's backup and a
  rollback is lost with the rollback. That only affects the timeline of a running call; the
  booking itself was frozen and is kept.
- Only the superuser can repair integrations during maintenance; an organisation admin who
  normally holds `integrations.manage` waits until it ends. This is deliberate: maintenance is a
  platform operator's window.
- Closures cannot be started or changed during maintenance. Maintenance is meant to be short;
  the runbook says to end it first if an emergency closure is needed.
- New: permission `system.maintenance` (regenerate `docs/authorization.md`), table
  `maintenance_state` (reversible migration), setting `MAINTENANCE_MODE` in `.env.example`, the
  CLI command, the maintenance page in `e2e/pages.json` (nl, en, with and without a contact block).
- The threat model gets the abuse cases: an organisation admin writing during maintenance, a
  citizen POST slipping past the middleware, and a machine-API write. Each gets a test. The
  authorisation matrix test runs every admin endpoint once with maintenance on.
- The runbook gets a section "Onderhoudsmodus" for when to switch it on, what still works, the
  environment override, and how it interacts with updates.
