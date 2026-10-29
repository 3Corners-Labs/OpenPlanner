# 0048. Live progress of updates: steps reported by the host updater

Status: proposed (2026-10-28), requested by the product owner. Extends ADR 0029 (updates from
the admin portal); amends ADR 0016 (admin pages without JavaScript) for one page.

## Context

After "Update now" the updates page said "the page shows the outcome as soon as the updater
reports it" and nothing else. The host updater only reported at the very end, and the page only
changed when the superuser reloaded it. An update takes from a few seconds to over five minutes
(the health wait), and during that time the superuser could not tell a request nobody picked
up from one that is pulling, backing up or waiting for health. The product owner called it
"waiting for nothing".

## Decision

1. **The updater reports each step before it takes it.** New command
   `python -m app.cli update-progress ID STEP` with the steps `pulling`, `verifying`,
   `backing_up`, `starting` (update) and `backing_up`, `migrating`, `starting` (restore);
   `rolling_back` may follow any of them. The request stores the last step and when it was
   reported (`update_requests.step`, `step_at`, migration 0027). Steps only move forward and
   must belong to the action's plan; a repeat changes nothing. Every new step is audited as
   actor `host-updater` (`update.verifying`, …).
2. **Reporting a step never stops the updater.** A version from before this ADR does not know
   the command, and during a restart the container may not answer; the updater ignores both.
   The step of a restore after the downgrade is reported by the version it returns to, because
   `web` is stopped while the schema goes down.
3. **The page shows the plan.** The "Open request" card lists the steps of the action, with
   "waiting for the updater" first, each marked done / in progress / not yet in text and shape
   (never colour alone), and when the current step began. A claimed request without a reported
   step is shown at its first step. The banner on the other admin pages names the current step;
   on the updates page itself the banner is left out, because the card says it all.
4. **Live, with a small own script instead of HTMX.** `static/update-progress.js` (same
   pattern as the slot picker's `calendar.js`, ADR 0020) asks
   `GET /admin/updates/progress?seen=KEY` every 5 seconds. The server answers `204` while
   nothing changed, otherwise the fragment, which the script swaps in; the new status line is
   announced in a polite live region, and focus inside the replaced part moves to the card's
   heading. Failed requests (web restarting) are retried; a redirect (session ended) or a
   fragment without a poll URL (the request ended) stops it. The page loads the script, and
   gets `script-src 'self'` in its CSP, only while a request is open. Without the script the
   card is the state at load time, with a "Refresh status" link. There is no automatic page
   reload (WCAG 2.2.1, 3.2.5).
   HTMX would need a vendored library, a dependency review and the same CSP change for one
   polling region; the own script is under 100 lines without dependencies.
5. **Same permission.** The fragment needs `system.update`, like the page.

## Settings

None added. Checked:

**Fixed on purpose:**

- The poll interval of 5 seconds is a property of the page, not of the installation: it does
  not change load in any meaningful way (one indexed read of at most 20 rows per open page)
  and no operator has a reason to tune it.
- The step names are a contract between `scripts/updater/` and the application; the
  database also checks them.
- The health wait stays `HEALTH_TIMEOUT` in the host updater's own configuration (ADR 0029).

## Consequences

- The superuser sees where an update is and since when; a request nobody picks up is visibly
  stuck at "waiting for the updater", next to the existing 10-minute warning.
- The first update *to* a version with this feature still shows no steps: the old container
  receives the reports and does not know them. From then on every update shows them.
- The admin portal now has one page with a script. CSP stays without scripts on every other
  admin page; the script has no strings, reads only same-origin fragments and inserts them
  with `DOMParser`, so autoescaped server HTML is all it ever shows.
- Migration 0027 is reversible; a restore to an older version drops the step columns.
- Docs: runbook "Updates", threat model (updates flow), architecture (module table). No manual
  topic exists yet for the updates screen; it is a superuser screen that explains itself.
- Maintenance mode (ADR 0038 §5) will add its own step to the plan when the updater switches it
  on before the backup.
