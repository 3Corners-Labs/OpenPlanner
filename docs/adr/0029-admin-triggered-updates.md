# 0029. Updates started from the admin portal, carried out by a host updater

Status: accepted (2026-10-26), requested by the product owner. Builds on ADR 0006 (signed
images) and ADR 0014 (authorisation matrix). Amended by ADR 0059 (§1: an installation on the
`rc` channel is also offered release candidates; `UPDATE_AUTO_APPLY`).

## Context

Images are built, signed and pushed to GHCR on every merge to `main` and every `v*.*.*` tag
(ADR 0006), but a running installation only changes when an operator logs in to the host and
runs `docker compose pull && up -d`. The product owner wants the portal to say when a new
version exists and to let a superuser start the update with one click, with a way back when the
new version misbehaves.

The `web` and `worker` containers run read-only, as a non-root user, without the Docker socket.
That stays: the socket in an internet-facing container is root on the host, and an application
that can replace its own image can be made to run any image. Unattended updaters such as
Watchtower are rejected for the same reason, and because they neither verify cosign signatures
nor deploy by digest.

## Decision

1. **Releases only.** OpenPlanner follows published GitHub releases (`v*.*.*` tags; drafts and
   pre-releases are ignored), not every merge to `main`. The image knows its own version
   through the `APP_VERSION` build argument; a build without a release version (`main`, local)
   is never offered an update, so a development install cannot be "updated" to an older release.
2. **Hourly check by the worker.** When `UPDATE_REPOSITORY` (`owner/repo`) is set, the worker asks
   `GET /repos/{owner}/{repo}/releases/latest` at most once per 60 minutes, through the
   `ReleaseSource` port (an httpx adapter; `UPDATE_CHECK_TOKEN`, optional, for a private
   repository, env only, read-only). The outcome (latest version, release page, when, or an error
   code) is stored in the one-row table `release_checks`. Without `UPDATE_REPOSITORY` nothing is
   checked and the portal shows no update features beyond the current version.
3. **One permission.** `system.update` (global level) is granted to the superuser only; it covers
   the banner, the page `/admin/updates` and every request. Requests need a fresh login
   (ADR 0015), are POST forms with the same-origin check, and are audited.
4. **Banner with snooze.** Every admin page shows holders of `system.update` a banner when the
   latest release is newer than the running version and no request is open: "version X is
   available (you run Y)", a link to the updates page and "remind me in 60 minutes". The snooze
   is per admin and kept in the signed session; it hides the banner, never the page. The banner
   also reports the outcome of the latest request until it is 24 hours old.
5. **A request queue, not an action.** "Update now" and "Restore previous version" only store an
   `update_requests` row (`requested`). At most one request is open (`requested` or `running`)
   at a time; an open `requested` one can be cancelled. Update targets must be newer than the
   running version. A restore is offered for 24 hours after a successful update, back to the
   version it replaced, and only while no newer update succeeded.
6. **The host updater carries it out.** A script with a systemd timer on the host
   (`scripts/updater/`) runs every minute. It talks to the application only through
   `python -m app.cli` inside the `web` container (`update-claim`, `update-report`,
   `backup-db`) and reports every step back, so the portal shows what happened.
   - *Update*: resolve `ghcr.io/<repo>:<version>` to a digest; `cosign verify` it against this
     repository's `main.yml` identity (refuse with `signature_invalid` otherwise); record the
     current digest and Alembic revision; back up the database with SQLite's online backup;
     pin the new digest in `.env.update` (compose reads `OPENPLANNER_IMAGE` from it); `docker
     compose up -d`; wait up to 5 minutes for `/readyz` and the worker's health.
   - *Safeguard*: if the new version does not become healthy, the updater rolls back on its
     own: stop `web` and `worker`, put the backup in place, pin the previous digest, start, and
     check health again. The request ends as `rolled_back` (or `failed` when even the way back
     is unhealthy, with a pointer to the runbook).
   - *Restore previous version* (manual): back up, run `alembic downgrade <previous revision>`
     with the current image (every migration is reversible, CI checks it), pin the previous
     digest, start and check health. Data entered since the update is kept. If that fails, the
     updater restores the backup it just took and returns to the newer version.
7. **Backups hold personal data.** They live only in the data volume (`data/backups/`, mode
   600), are never copied elsewhere by the updater, and are deleted after 24 hours on every
   updater run. A restored backup is minutes old; purge jobs that ran after it are still pending
   in it and run again, so no purged data comes back for longer than one worker cycle.

## Consequences

- The application gains no new privilege: it reads a public (or token-protected) release list
  and writes rows. Taking over the portal lets an attacker move only between signed releases of
  this repository, within one permission held only by the superuser, and every step is audited.
- An update restarts `web` and `worker`: a few seconds without the booking pages. The updater
  does not schedule updates for quiet hours yet; the superuser chooses the moment.
- An automatic rollback after a failed health check may lose bookings made in the minutes the
  new version was half up; the failure window is bounded by the 5-minute health wait. The manual
  restore loses nothing.
- Installations without the updater (no systemd, other hosting) still see the banner; requests
  stay `requested` and the page says that no updater has picked them up after 10 minutes.
  `docs/runbook.md` keeps the manual procedure.
- New settings: `UPDATE_REPOSITORY` and `UPDATE_CHECK_TOKEN` (application), the `APP_VERSION`
  build argument set by `main.yml`, and the host updater's own `/etc/openplanner/updater.env`
  (`COMPOSE_DIR`, `UPDATE_IMAGE`, `CERT_IDENTITY_REGEXP`, `HEALTH_TIMEOUT`). Compose reads the
  image from `OPENPLANNER_IMAGE` in `.env.update`; development keeps `openplanner:local`.
- No new dependency: the release lookup uses `httpx`, backups use the standard `sqlite3`
  module; the host needs `cosign`, which ADR 0006 already requires for deployment.
