# 0116. One edition, maintenance lines on demand, and tested upgrade paths

Status: accepted (2026-11-05, the product owner's approval of the proposal in the conversation,
`po:approved` on #519; the three decisions of the research note §10 are taken as recommended:
window `N-1` with public patch releases, the line mechanics now with one dry run, one edition
with services sold). Not built. Amends ADR 0052
(point 6: a support window and maintenance lines; point 2: a migration raises MINOR), ADR 0108
(point 1: every `vX.Y.Z` release is public), ADR 0067 (point 1: auto-apply in production
inside a window) and ADR 0114 (point 11: the channel level `minor` is withdrawn in favour of a
line pin). Extends ADR 0029, 0050, 0059, 0079 (the mechanics it reuses), ADR 0036 (one writer:
rings, not traffic canaries), ADR 0054 (the licence that shapes the commercial model) and ADR
0099 (the configuration document as the portable configuration). The analysis, the options
and the comparable practice are in `docs/research/release-and-update-models.md`.

## Context

OpenPlanner will run in three ways: private (a customer's own datacenter, possibly offline),
dedicated (one installation per customer, hosted by us) and perhaps shared (one installation
serving several customers as organisations). All run the same signed images on one SQLite
file with one writer (ADR 0036). The update machinery exists and works: portal request, host
updater with signature check, backup, digest pin, health check and rollback (ADR 0029, 0050,
0067); a candidate per merge (ADR 0059); the release as a workflow (ADR 0079); the lab host
updated itself to `v0.4.0` this way.

What is missing is policy, and it shows in four places. Every merge is a candidate, so a
"patch" cut from `main` carries every feature since the last release (ADR 0052 point 2 then
makes it a MINOR): a customer in production cannot receive one security fix without
everything else. Patch images are private (ADR 0108), so a security fix needs a pull secret
while the release it fixes does not. CI applies every migration up and down on a fresh
database but never on a database a released version wrote. And "only the latest release is
supported" (ADR 0052 point 6, `SECURITY.md`) stops being true the day two customers run two
minors. The product owner also asks how controlled updates and migrations become paid value
without gating security.

## Decision

1. **One edition.** There is one codebase and one build; every release is EUPL-1.2 and free,
   patches included. Nothing in the application enforces an edition. What is sold is work and
   risk: managed updates in the hosted models, a tested upgrade path for a named installation,
   an extended maintenance line, a service level and a migration service. Security fixes are
   never behind a payment: they are on `main` and on every supported line.
2. **Support window `N-1`, from 1.0.0.** The newest minor and the one before it receive
   security fixes; `SECURITY.md` says so, and the portal shows the running line's status
   from a signed release index (point 9). A customer who needs a longer window buys an
   *extended line*: the same branch, kept longer, with the backports and the tested path
   from it. Before 1.0.0 the window stays "latest only" (ADR 0052 point 6).
3. **Maintenance lines on demand.** `release/X.Y` is cut from the tag `vX.Y.0` only when a
   fix must ship and `main` holds a `feat` since that tag; otherwise the patch comes from
   `main` as today. A fix is merged to `main` first; the label `backport:X.Y` makes Claude
   Code open the cherry-pick PR on the line, which runs the full lane in the merge queue
   under the same ruleset as `main`. A push to `release/X.Y` builds candidates
   `vX.Y.Z-rc.N` exactly as `main` does (ADR 0059), and the release workflow (ADR 0079)
   accepts a line for `release-pr` and `tag` when the version is a patch of that line; ADR
   0052 point 4's check becomes "the tagged commit is on `main` or on `release/X.Y`, and the
   version belongs to that line". The line's `CHANGELOG.md` section is merged back into
   `main` by the next release PR.
4. **A patch release carries no migration.** `scripts/release_pr.py` refuses PATCH when
   `migrations/versions/` changed since the last tag; a migration raises MINOR. A patch is
   therefore an image swap in both directions, and a line never diverges in schema from its
   `vX.Y.0`.
5. **Expand/contract for every new migration.** A migration in minor N creates, adds
   nullable or defaulted columns, adds indexes and backfills; it drops, renames or tightens
   only what minor N-1 already stopped using. Persisted names (settings keys, event and job
   kinds, column names) follow the same two steps: write the new name, read both, drop the
   old one a minor later. Migrations are self-contained (no import from `app/` beyond pure
   helpers; an import-linter contract fails CI otherwise), so any chain of released versions
   runs in order. "Restore the previous version" (ADR 0029 §6) then normally starts the
   previous image on the current schema; `alembic downgrade` stays the tested fallback.
6. **A minimum upgrade-from version per release.** `MIN_UPGRADE_FROM` is baked into the
   image next to `APP_VERSION`. The updater and the application at start refuse to migrate a
   database whose last `running_versions` row is older (`upgrade_path_required`, naming the
   stop). The first stop is 1.0.0; raising it is a line in the release PR. Within the window
   a supported installation is at most two minors behind; a longer jump is possible but is
   only promised, and tested, as a paid upgrade path.
7. **An upgrade matrix in CI from released data.** The release job runs the released image
   on a fresh database with `python -m app.cli seed --upgrade-fixture` (generated data only:
   organisations, groups, slots, bookings in every status with made-up personal data, jobs,
   audit events, closures, settings of every class, an identity provider, API clients) and
   attaches `upgrade-fixture-X.Y.Z.sqlite` with its SHA-256 to the release. On every PR that
   touches `migrations/` or `app/adapters/db/`, and in the merge queue, a job takes the
   fixture of every minor in the window (and of the stop): `alembic upgrade head`, start,
   smoke tests and invariants (counts per table, personal data present on live and absent on
   purged bookings, pending jobs claimable, settings readable), `alembic downgrade` to the
   fixture's revision, start the *released* image on it, upgrade again. The same job measures
   the schema step on a large generated fixture (100,000 bookings) and fails above the budget.
8. **Long data migrations run as jobs.** A schema step that cannot finish within the budget
   on the large fixture is split: the expand step in the migration, the data step as a job
   kind in the existing `jobs` table that works in batches under the write lock, is
   idempotent, shows progress on `/admin/updates`, and must be finished before the contract
   step of a later minor (that migration refuses with `data_migration_pending`). Maintenance
   mode (ADR 0038) covers the schema step, as the updater already does.
9. **Lines and windows in the portal.** `update_line` (class C, empty or `X.Y`) makes the
   worker offer only patches of that line; empty follows everything in the channel as today.
   The channel level `minor` of ADR 0114 point 11 is withdrawn: it skipped patches, and
   existed because patch images were private. `update_window` (class C, weekday and hour
   ranges in the installation's time zone, default any time) bounds when the worker turns a
   newer version into a request and when the updater claims a waiting one; a superuser's
   click is immediate as today, and a request may carry `apply_after`. Auto-apply becomes
   usable in production *only* with a window set (amending ADR 0067 point 1). The release
   index carries `support.json` (lines, their status, until when), signed with the release,
   and the portal shows the running line's status next to the version.
10. **Every release is public.** `main.yml` pushes every `vX.Y.Z` to the public packages of
    ADR 0108; candidates stay private. The release audit of ADR 0078 still runs for `vX.Y.0`
    only.
11. **Offline delivery as a signed bundle.** For every release CI produces one archive: the
    OCI layout of the application and proxy images, the cosign bundles for offline
    verification, both SBOMs, the Helm chart, the release notes and the signed `index.json`
    with `support.json`. `scripts/install.sh --bundle FILE` and the updater with
    `UPDATE_SOURCE=file://…` load it into the host's image store or a local registry and verify
    with the same identity offline; the worker's release check reads `index.json` from
    `UPDATE_INDEX_URL` (class B) instead of GitHub. A customer with a registry mirror pushes the
    layout there and points `UPDATE_IMAGE` at it. Offline verification is proven on a
    disconnected VM before it is promised.
12. **Hosted models update in rings, by installation.** Ring 0 the lab host (`rc`), ring 1
    our demo and the customers' test installations (the hour of the release), ring 2
    dedicated customers in their windows, ring 3 shared cells with the canary cell first. A
    ring is a runbook convention and two settings per installation (`update_window`,
    `update_line`); there is no traffic canary inside an installation (one writer, ADR 0036).
13. **Backups per model, bounded by the purge.** The updater's backup stays the safety net
    of a switch (24 h, data volume). The regime of an installation is the operator's: in the
    private model the customer's, with `python -m app.cli backup-db` on a timer in the
    runbook; in the hosted models ours, a nightly encrypted copy off the host or a volume
    snapshot, retention no longer than the longest hold of `app/domain/retention.py` plus 24
    hours (a restore from an older backup brings back deleted personal data), a restore tested
    every quarter, both written into the hosting contract and `docs/dataflow.md`.
14. **The database file is the portable full-data format.** Moving an installation between
    models is `backup-db`, the bootstrap keys of ADR 0042 §9, the class B environment and
    `alembic upgrade head` on the target, written as the runbook section *Move an
    installation* and offered as a service. Configuration travels as the document of ADR
    0099 (ADR 0089's package until then). No second full-data format is built.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `update_line` | C platform | empty | empty, or `X.Y` of a listed release | `settings.platform.manage` | Point 9: follow patches of one minor only; replaces the `minor` level of ADR 0114. Env locks. |
| `update_window` | C platform | empty (any time) | up to 7 ranges `weekday hh:mm–hh:mm` in the installation's time zone; a range at least 30 minutes | `settings.platform.manage` | Point 9: when the worker requests and the updater claims an automatic update; a click is immediate. Env locks. |
| `update_auto_apply` | C platform (ADR 0067) | `false` | bool; in production usable only with `update_window` set | `settings.platform.manage` | Point 9: amends ADR 0067 point 1. |
| `update_channel` | C platform (ADR 0059, 0067) | `release` | `release`, `rc` | `settings.platform.manage` | Point 9: the `minor` level of ADR 0114 point 11 is not added. |
| `UPDATE_SOURCE` | B deployment (host, `updater.env`) | `registry` | `registry`, `file://<path>` | none (host) | Point 11: where the updater takes images from; tied to the host's network. |
| `UPDATE_INDEX_URL` | B deployment | empty (GitHub releases) | an `https://` URL or a `file://` path on the worker's volume | none (env) | Point 11: the release index for a host without GitHub; tied to the host's network. |
| `MIN_UPGRADE_FROM` | B deployment (build) | `1.0.0` | a released version | none (baked into the image) | Point 6: describes the image, like `APP_VERSION`. |

**Fixed on purpose:** the window `N-1` and "from 1.0.0" (a policy in `SECURITY.md`, not a
behaviour); the rule "no migration in a patch" and the expand/contract rule (release process,
checked in CI); the migration budget (300 s on the reference fixture, a CI constant mirroring
`HEALTH_TIMEOUT`, which stays the host's tunable); the fixture sizes (a parameter of the seed,
not of the product); the 24 h of the updater's backup (ADR 0029); the ring order (runbook).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed, with `docs/research/release-and-update-models.md` | #519 | merged 2026-11-05 |
| 1 | Decision texts: `SECURITY.md` support window; amendments noted in ADR 0052, 0067, 0108, 0114; CLAUDE.md migration rule; PR template question on expand/contract | #533 | in PR #533 |
| 2 | Rules in code: `release_pr.py` refuses PATCH with a migration; import-linter contract for self-contained migrations | | planned |
| 3 | Upgrade fixture and matrix: `seed --upgrade-fixture`, fixture attached to every release, matrix job with downgrade-and-start-the-released-image and the duration budget | | planned |
| 4 | Lines: candidates on `release/X.Y` in `main.yml`, the release workflow on a line, the tag check, ruleset for `release/*`, label `backport:X.Y` and the cherry-pick PR; one dry run on the current line | | planned |
| 5 | Portal and updater: `update_line`, `update_window` with `apply_after`, auto-apply in production with a window, support status from `support.json`, `MIN_UPGRADE_FROM` check; manual topic *Updates* | | planned |
| 6 | Public patch images: `main.yml` pushes every `vX.Y.Z` to the public packages | | planned |
| 7 | Offline bundle: `release-bundle` job, `install.sh --bundle`, `UPDATE_SOURCE`, `UPDATE_INDEX_URL`, offline verification proven on a disconnected VM, runbook | | planned |
| 8 | Background data migrations: the job kind, progress on `/admin/updates`, `data_migration_pending` | | planned |
| 9 | Docs and contracts: runbook *Move an installation*, *Backups per model*, *Rings and windows*; `docs/dataflow.md`; editions and support table in the README and on the website; admission policy in `docs/kubernetes.md` | | planned |

## Consequences

- A production customer can receive one security fix without the features of the next
  minor, and can see in the portal whether their line is still supported. The price is a
  branch per line in use, a cherry-pick per fix and a second lane of candidates, paid only
  when a line exists.
- Security fixes stay free and public by construction (points 1, 2, 10); what is sold is
  time and distance. This matches the licence (ADR 0054: services may be sold, the code may
  not be withheld from users of the hosted service) and the direction of the Cyber Resilience
  Act (security updates free of charge for the support period; see the research note §8.6,
  unverified against the Official Journal).
- Every new migration costs a little more thought (expand now, contract next minor) and
  every release a fixture; in return downgrade becomes a restart, skip-version upgrades are
  a chain CI has run, and the matrix is bounded by the window.
- The lab host is unaffected: no line pin, channel `rc`, every merge.
- Kubernetes installations get the line and window as information and a `helm upgrade`
  command (ADR 0114 step 4); the admission policy is documented, not shipped.
- The threat model gains: "a hand-made `release/X.Y` branch or tag" (the ruleset and the tag
  check), "a bundle altered in transit" (offline verification with the same identity), "a
  window that never opens" (the portal says so after the first missed window), "a fixture
  with real personal data" (the seed generates; CI refuses a fixture not produced by the
  release job). `docs/dataflow.md` gains the backups per model and the bundle.
- ADR 0052 point 6, ADR 0067 point 1, ADR 0108 point 1 and ADR 0114 point 11 are amended by
  this ADR; they are not rewritten.
- No new dependency in the application. The bundle job uses `crane` or `oras` in CI only
  (to be chosen in step 7 and recorded in `docs/dependencies.md`); the host needs `cosign`
  as before.
