# 0067. Version management in the portal: channel, chosen version, history

Status: accepted (2026-10-30), requested by the product owner. Amends ADR 0059 (points 4 and
6: the channel and auto-apply move from class B to class C; the updater's identity follows the
installer mode) and ADR 0029 (a chosen version instead of "the latest"; the history comes from
the audit log). Extends ADR 0031 and 0040 (audit filter by subject) and ADR 0048.

## Context

ADR 0059 put the update channel and auto-apply in the environment (class B), reasoning that
they describe the host. The first switch of docker01 to the `rc` channel then needed a shell on
the host and an installer flag. The product owner's reaction on 2026-10-30: version management
belongs in the portal, because the containers are often run by another team than the
application administrators. Every step that needs the host team breaks the administrators'
loop.

Two more gaps showed on the same day:

- **Choosing.** The portal offers only "update to the latest" and "restore the previous
  version within 24 hours". A superuser cannot pick a specific candidate or release, and
  cannot see what a version contains without leaving the portal.
- **History.** The updates page lists its own `update_requests` table without the requester,
  and a version change the application did not request (the installer pinning a version, a
  rollback by hand) leaves no trace at all. The product owner wants the history of updates,
  candidates included, in the global audit log, with the administrator who did it, and the
  updates page as a filtered view of that log.

What the host side already has stays: the updater (ADR 0029) carries out whatever the
application requests, verifies the signature, backs up and rolls back.

## Decision

1. **Channel and auto-apply are portal settings (class C).** `update_channel` and
   `update_auto_apply` move to class C, topic *Updates*, changed by the superuser with
   `settings.platform.manage` on the settings page and through controls on `/admin/updates`.
   The production rule stays in the server: with `ENVIRONMENT=prod`, the portal value `rc` or
   auto-apply on is *not usable* (ignored and flagged as outside the bounds, as ADR 0042 §2
   does for any value that breaks a rule), so production follows releases on a superuser's
   click whatever is stored. An environment variable still locks the value (ADR 0042 §2) for
   an operator who wants it fixed. The web process and the worker read both values live.
2. **The host decides only whom to trust, once.** The updater's signing identity follows the
   installer **mode**: `production` accepts images signed by `main.yml` for a release tag only;
   `test` and `lab` also those signed on `main`, where candidates are built. The installer's
   `--channel` flag (ADR 0059 step 2) goes; `--version X.Y.Z-rc.N` stays for a first pin on a
   test or lab host. After the installer has run once, nothing on the host changes for a
   channel switch, a chosen version or auto-apply.
3. **The superuser chooses the version.** Each hourly check stores the channel's recent
   versions (`release_listing`: version, release page, published at, pre-release flag, and
   the generated notes as plain text, at most `NOTES_CHARS`). `/admin/updates` lists the
   versions newer than the running one, newest first, each with its notes (the PR titles and
   numbers GitHub generated), and "Install this version". `request_update` takes a target;
   the domain accepts a target only when it is listed and newer than the running version
   (forward only: the downgrade protection of the threat model stands). Going back stays
   "Restore the previous version" within `RESTORE_WINDOW`, because a downgrade must undo
   migrations and that path exists and is tested. "Update now" on the banner keeps meaning the
   newest listed version.
4. **Every version change is in the audit log, with who did it.** Requests, the updater's
   steps and outcomes are already audited under `system.update` (actors: the superuser's
   subject, `host-updater`, `system` for auto-apply). New: at start-up the web process
   compares its `APP_VERSION` and `APP_COMMIT` with the last row of `running_versions`; when
   they differ it adds a row and a global audit event `system.version` with detail
   `version.changed`, `from:<version>`, `to:<version>`, `commit:<12 hex>`, actor
   `host-updater` when a running request targets this version (the updater's switch), else
   `host` (installer, rollback by hand). Candidates and releases are logged alike.
5. **The history on the updates page is the audit log, filtered.** The *History* section shows
   the global events with action `system.update` or `system.version`, newest first, with a
   *By* column: the staff member's display name (ADR 0046), `system`, `host-updater` or
   `host`. The `update_requests` table remains the updater's work queue and the source of the
   live progress (ADR 0048); it is no longer shown as the history. The audit page gains a
   filter *subject* (`AuditFilter.subject`: updates, login, rights, settings, bookings, …,
   each a set of action prefixes), and the updates page links to the audit page with
   `subject=updates`.
6. **A version change outside the portal is visible, not silent.** When point 4 records a
   change with actor `host`, the updates page says so above the history ("version changed
   outside the portal on <date>"), until the next change.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `update_channel` | C platform (was B, ADR 0059) | `release` | `release`, `rc`; `rc` not usable with `ENVIRONMENT=prod` | `settings.platform.manage` | Live; env locks. |
| `update_auto_apply` | C platform (was B) | `false` | bool; `true` not usable with `ENVIRONMENT=prod` | `settings.platform.manage` | Live; env locks. |

**Fixed on purpose:** `LISTED = 30` versions per check and `NOTES_CHARS = 4000` of notes per
version (protocol and storage details, not behaviour an administrator tunes); the hourly
`CHECK_INTERVAL` and the 24-hour `RESTORE_WINDOW` (ADR 0029); the audit subjects (a fixed
mapping of action prefixes). The installer mode decides the signing identity (ADR 0006): a
trust decision of the host team, never a portal setting.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | `update_channel` and `update_auto_apply` class C, read live by web and worker; production rule in `usable`; installer identity by mode, `--channel` removed; settings docs | #145 | in PR |
| 2 | `running_versions` and `system.version` at start-up; history from the audit log with *By*; audit filter *subject*; "changed outside the portal" notice | #146 | in PR |
| 3 | `release_listing` with notes; version picker and "Install this version"; `request_update(target)` | | planned |
| 4 | docker01: channel switched in the portal, a chosen candidate installed, history shows who | | planned |

## Consequences

- The host team installs the updater once (installer mode = trust). Everything else is the
  superuser's, in the portal, audited.
- Two settings change class from B to C, two days after they were introduced; `docs/settings.md`
  follows from the registry. An installation that set `UPDATE_CHANNEL` in `.env` keeps
  working: the environment locks the portal value.
- Migrations: `release_listing` and `running_versions` (reversible, no personal data).
- The threat model gains: "a production installation moved onto candidates from the portal"
  (not usable in production, env lock), "a chosen version that is not in the listing or older
  than the running one" (refused by the domain), "a version change outside the portal that
  nobody notices" (audited as `host`, shown on the page).
- The updates page becomes the single place for version management: what runs, what is
  available and what it contains, who changed what and when, and the channel and auto-apply
  switches. The admin manual gets the topic *Updates* (ADR 0033), which it lacked.
- The first switch of docker01 (0.3.0 → 0.4.0-rc.5, 2026-10-30) needed the installer because
  0.3.0 predates ADR 0059; from rc.5 on, this ADR makes that a portal action.
