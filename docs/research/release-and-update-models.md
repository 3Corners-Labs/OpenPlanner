# Release and update models across three ways of running OpenPlanner

Research note, 2026-11-05, for the product owner. It proposes; it builds nothing. The
decision it asks for is drafted as ADR 0116 (`docs/adr/0116-one-edition-maintenance-lines-on-demand-and-tested-upgrade-paths.md`).
Everything here was checked against the repository at `main` after the `v0.4.0` release and
against the public documentation of comparable products on 2026-11-05; a claim that could not
be verified says so.

## 1. In one page

OpenPlanner will run in three ways: **private** (a customer's own datacenter, possibly
without internet), **dedicated** (one installation per customer, hosted by us in a cloud) and
perhaps **shared** (software as a service: several customers on one installation, which the
authorisation matrix already allows). All three run the same signed images on one SQLite
file with one writer (ADR 0036), so the differences are in *who triggers an update, when,
from where, and who carries the risk*, not in the application.

What exists already covers the first model well: the portal offers a release, the host
updater verifies the signature, backs up, switches by digest, checks health and rolls back
(ADR 0029, 0050, 0067); every merge is a candidate on the `rc` channel (ADR 0059); the release
is a workflow with an audit (ADR 0079); the lab host updated itself to `v0.4.0` this way and
worked. What is missing is a **policy** for lines, windows and migrations, and five pieces of
machinery that follow from it.

Recommendation, in short:

1. **One edition, one codebase, everything EUPL-1.2.** No open core, no gated features. What
   is sold is work and risk: managed updates in the hosted models, tested upgrade paths beyond
   the free window, backports to an older line, a service level, and a migration service.
   Security fixes reach every installation for free within the support window.
2. **Support window `N-1`**: the current minor and the one before it receive security fixes;
   a longer window per customer is a paid extended line. Before 1.0 the window stays "latest
   only" (ADR 0052 point 6), and the mechanics are built and exercised once.
3. **Maintenance branches on demand, not permanently.** `release/X.Y` is cut from the tag
   `vX.Y.0` only when a fix must ship while `main` holds unreleased features. Fixes land on
   `main` first and are cherry-picked; the branch gets its own candidates `vX.Y.Z-rc.N` and
   releases through the same workflow. This is the product owner's "0.4.1 RC construction",
   with the branch created lazily so that most minors never need one.
4. **A patch release never changes the schema.** Migrations raise MINOR. With that rule a
   patch is always safe to apply and to undo, and the upgrade matrix is bounded by minors.
5. **Expand/contract for every migration**, self-contained migration files, a declared
   *minimum upgrade-from version* per release, and an **upgrade matrix in CI** from every
   supported minor on a realistic fixture database, including a start of the previous image on
   the downgraded schema.
6. **Patch images become public** (amend ADR 0108: every `vX.Y.Z`, candidates stay private),
   so a security fix needs no pull secret anywhere.
7. **Offline delivery as a signed bundle** (OCI layout, signatures, SBOM, chart, release index)
   that the installer and the updater read from a file or a local registry mirror, verified
   offline with the same identity.
8. **Hosted models update in rings**: lab (`rc`) → our own demo → dedicated customers in
   waves inside their window → shared cells last. Per installation the update is the same
   updater or `helm upgrade`; a canary is a ring, not a traffic split (one writer, one file).

Three decisions are needed from the product owner (§10): the support window and public patch
images; branches on demand now (with one dry run on `release/0.4`) or only after 1.0; and
"one edition, services sold" as the commercial model.

## 2. Where we stand

| Piece | ADR | State on `main` (2026-11-05) |
|---|---|---|
| Release = tag `vX.Y.Z` on `main`, SemVer from Conventional Commits, security fix released the same day, "only the latest release is supported", no maintenance branches before 1.0 | 0052 | built |
| Candidate `vX.Y.Z-rc.N` on every merge; channels `release` and `rc` | 0059 | built; docker01 on `rc` with auto-apply |
| Portal: chosen version, history, check interval, channel and auto-apply as class C | 0067 | built except the docker01 demonstration row |
| Host updater: cosign verify by identity, backup, pin by digest, health, automatic rollback, 24 h restore with `alembic downgrade` | 0029, 0050 | built; `v0.4.0` applied this way on the lab host |
| Release as a workflow: release PR, audit for `vX.Y.0`, tag by dispatch, release app | 0079, 0107 | built |
| Public images for `vX.Y.0` only; patches and candidates private | 0108 | built (step 2 is an administrator's act) |
| Configuration export/import (SQLite package → JSON document with plan/apply) | 0089, 0099 | 0089 built; 0099 proposed, not built |
| Kubernetes: one replica, Helm chart, updates by the cluster's tooling, chart to be published for every version, channel level `minor` proposed | 0114 | steps 0–2 built; trial, `minor` level and chart publishing open |
| Scaling envelope: SQLite, one writer, triggers for the step beyond one VM | 0036 | policy; seams not built |
| Migrations: Alembic, linear chain `0001`…`0089`, every migration reversible, CI applies up and down on a fresh database, `web` runs `alembic upgrade head` at start | CLAUDE.md | built; no upgrade test from an older *released* database |

Lessons that shape the proposal:

- **Persisted names are a contract.** Settings keys, event kinds, job kinds and column names
  live in customers' databases; renaming one is a data migration with both names readable
  for at least one minor. (Seen in the terminology rename of ADR 0077 and the history rewrite
  of ADR 0115.)
- **The by-digest, verified, backed-up update works end to end** (lab host, `v0.4.0`). The
  updater is the asset to extend, not to replace.
- **Every merge is a candidate, so a patch cut from `main` is a minor in disguise.** ADR 0052
  point 2 already says so: as soon as one `feat` is on `main`, the next number is MINOR. A
  real patch exists today only when nothing but fixes merged since the last tag, which with a
  continuous `main` is rarely true. A customer in production who needs one security fix would
  receive every feature merged since their release.
- **Patches need a pull secret** (ADR 0108). Today a security fix is harder to obtain than
  the release it fixes.
- **The configuration package is tied to a schema revision** (ADR 0089); ADR 0099 fixes that
  with a versioned document. Full data has no portable format other than the database file
  itself, which is fine (§6.7).

## 3. The three models

| | Private (on-premises) | Dedicated (single-tenant, hosted by us) | Shared (software as a service) |
|---|---|---|---|
| Who operates the host | the customer's operations team | us (or a hosting partner), one VM or one Kubernetes namespace per customer | us; one installation ("cell") serves several customers as organisations, or several cells |
| Internet from the host | maybe none (air-gapped), or outbound only | yes | yes |
| Who triggers an update | the customer's superuser in the portal (ADR 0029); auto-apply refused in production (ADR 0067) | our operations, inside the customer's agreed window, by the same updater or `helm upgrade`; the customer's superuser may still click | our operations, in waves across cells |
| Maintenance window | the customer's; a portal setting (§4.2) | contractual, per customer; stored in the same setting | ours, announced; the quietest hours of all organisations on the cell |
| Staged rollout | none inside one installation; the customer may run a test installation | rings across installations (§4.3) | rings across cells; a canary cell with our own demo organisation first |
| Version pinning | by digest, always (ADR 0029); a line pin (`0.4`, §4.4) keeps them on patches of one minor | by digest; the customer's contract may pin a line | one line for all cells; a cell never lags more than one minor |
| Rollback | updater: automatic on failed health; manual restore within 24 h | the same, plus volume snapshots | the same, per cell; plus snapshots |
| Offline delivery | a signed bundle (§4.5) through the customer's registry mirror or a file | not needed | not needed |
| Signature and SBOM checks | cosign by identity in the updater; offline verification with the bundle; SBOM delivered with the bundle | cosign in the updater or an admission policy on Kubernetes | the same |
| Backups hold personal data | in the customer's hands; the updater's backups stay in the data volume for 24 h | ours; encrypted, retention aligned with the purge (§6.6) | ours; the same, per cell |
| Where the service level lives | support contract: tested upgrade path, backports | hosting contract: windows, rollback, restore time | hosting contract per customer, one platform |

The shared model needs one honest statement: the application has one SQLite file and one
writer per installation (ADR 0036), so there is no rolling update, no blue/green and no traffic
canary *inside* an installation. A staged rollout is a sequence across installations. That is
enough for the sizes in view (ADR 0036: about 80 bookings per working day per installation) and
it keeps the hosting cheap; the step beyond it is ADR 0036's own ADR, not this one.

## 4. Question 1: update control per model

### 4.1 Who triggers, and when

| Option | Private | Dedicated | Shared | Trade-off |
|---|---|---|---|---|
| A. Superuser clicks in the portal (today) | yes | possible, but the customer rarely wants to | no | simple, audited; nobody plans the moment |
| B. Auto-apply at once (today, not in production) | labs only | no | no | fast; no window |
| C. **Auto-apply inside a window** (new: `update_window`, class C) | optional, chosen by the customer | default | default | the update happens when agreed, without a person at the keyboard; needs a clear "what if the window is missed" rule |
| D. Scheduled by hand ("apply at …" on the request) | useful | useful | less (waves) | one more state on `update_requests`; explicit |

Recommendation: C, with D as a small addition (a request may carry `apply_after`). The updater
already polls every minute and claims a request; a window is a condition on the claim, nothing
else moves. Production keeps the rule that *something* a person chose starts the update: either
the click (A) or the window (C) that the superuser set. The ADR 0067 rule "auto-apply not usable
in production" is relaxed to "auto-apply in production needs a window", so an unattended
production installation can still be kept current.

### 4.2 Maintenance window

A window is a set of weekday and hour ranges in the installation's time zone (platform
setting, class C, default empty = any time). The worker turns a newer version into a request
only inside the window (as `update.auto_request` does now), and the updater claims a waiting
request only inside the window, unless a superuser clicked "Update now" (then at once, as
today). A window that was missed (the host was down) is caught at the next one. Maintenance
mode (ADR 0038) already covers the minutes of the switch.

### 4.3 Staged rollout for the hosted models

Rings, by installation, not by traffic:

| Ring | What | When |
|---|---|---|
| 0 | the lab host, channel `rc`, auto-apply (today) | every merge |
| 1 | our own demo installation and the customer-facing test installations, channel `release`, auto-apply, window "any" | the hour the release is published |
| 2 | dedicated customers whose window is open, in order of their window | from the next window |
| 3 | shared cells, the canary cell (with our own organisation) first | after ring 2 is green for a working day |

A ring is a convention in the runbook and a value in each installation's configuration
(`update_window`, `update_line`), not a feature of the application. "Green" is the health
check plus the walk-through of `docs/runbook.md` on ring 1, by a person, as the release
checklist already asks. Nothing in this is new code except the window.

### 4.4 Version pinning and lines

Today an installation is offered "newer than running" within its channel; a customer on 0.4.0
is offered 0.5.0 the day it exists. Once maintenance lines exist (§5), a customer must be able
to say "stay on 0.4, patches only". Options:

| Option | Meaning | Trade-off |
|---|---|---|
| A. Channel level `minor` (ADR 0114 point 11) | follow `X.Y.0` only, skip patches | wrong direction: it skips security fixes; it exists because patch images are private |
| B. **Line pin** `update_line` (class C, empty or `X.Y`) | follow the newest *patch* of that minor; empty follows everything | one setting with one meaning; the superuser moves to the next line deliberately |
| C. Pin by exact version only | nothing is offered | what the installer does with `--version`; too static |

Recommendation: B, and withdraw the `minor` level of ADR 0114 point 11 once patch images are
public (§8.4); a GitOps tool expresses the same as `~0.4.0` on the chart. The portal shows,
next to the running version, the line and its support status ("0.4 receives security fixes
until 0.6.0 is released"), from a small signed `support.json` in the release index (§4.5), so
an installation on an unsupported line sees it without anybody mailing them.

### 4.5 Offline and air-gapped delivery

The private model may have no route to `ghcr.io` or `api.github.com`. Three options:

| Option | How | Trade-off |
|---|---|---|
| A. Customer's registry mirror | the customer mirrors `ghcr.io/<owner>/openplanner` (Harbor, Artifactory, `registry:2`) with the signatures and attestations (cosign stores them as OCI artifacts next to the image); `UPDATE_IMAGE` points at the mirror | standard tooling on their side; the release *index* (which versions exist) still comes from GitHub, so the portal's check needs a second source |
| B. **Signed release bundle** | CI produces, per release, one archive: OCI layout of the application and proxy images (`oras`/`crane`-compatible), the cosign bundles for offline verification, both SBOMs, the Helm chart, the release notes and a signed `index.json` (versions, digests, support status); the customer carries it in; `scripts/install.sh --bundle FILE` and the updater with `UPDATE_SOURCE=file://…` load it into the host's local image store (or a local registry) and verify with `cosign verify --offline` against the same identity | one artefact, one procedure, verifiable without network; the bundle must be made by CI only (its contents are what the signature covers) |
| C. Export from a connected installation | an operator exports the running image from a connected host | ad hoc; no index, no SBOM; rejected |

Recommendation: B, with A as the documented alternative for customers who already run a
mirror (the bundle's OCI layout can be pushed into a mirror with `crane push`). The portal's
release check gains a `ReleaseSource` adapter that reads `index.json` from a path or an
internal URL (`UPDATE_INDEX_URL`, class B: tied to the host's network); the GitHub adapter
stays the default. The SBOMs travel in the bundle, which is also what a customer's security
office asks for.

Cosign can verify offline when the bundle (certificate, signature, Rekor entry) is shipped
with the image; the host still needs the Sigstore root of trust (`cosign initialize` once,
updatable through the same bundle). This is standard Sigstore practice, to be verified on a
disconnected VM in the build step before it is promised to a customer.

### 4.6 Signature and SBOM checks per model

- **Private and dedicated on a VM**: unchanged, the updater verifies by identity before it
  pins (ADR 0029 §6); offline with the bundle.
- **Kubernetes** (any model): the chart documents an admission policy (Sigstore
  `policy-controller` or Kyverno `verifyImages`) with the same identity, so that no unsigned
  image runs in the namespace; the runbook already has the manual `cosign verify`. Not built;
  ADR 0114 step 3's trial is where it is tried.
- **SBOM**: attached to every release and in the bundle; a customer who wants to scan it does
  so with their own tooling (`grype`, as CI does). Nothing new.

## 5. Question 2: patch lines

### 5.1 The problem

`main` moves continuously, every merge is a candidate, and a release is whatever `main` holds.
That gives speed and one truth, and it means that the only way to ship *one* fix to a customer
on 0.4.0 is to ship them 0.5.0. For a lab that is right; for a production installation with a
change board it is not, and the moment a second customer runs a different minor, "only the
latest release is supported" (ADR 0052 point 6, `SECURITY.md`) stops being a policy and
becomes a promise we cannot keep.

### 5.2 Options

| Option | How | Cost | Fits |
|---|---|---|---|
| A. Status quo: patch = next number from `main` | nothing | none | before 1.0, one customer, labs |
| B. **Permanent maintenance branch per minor** (`release/0.4`, "0.4.1 RC construction") | cut at every `vX.Y.0`; fixes cherry-picked from `main`; the branch builds its own candidates `v0.4.1-rc.N`; release PR and tag on the branch | every minor: a branch, its CI, its changelog section, its candidates; every fix: a cherry-pick PR with the full lane; the release workflow must run on a non-default branch; the tag check must accept a tag on a `release/*` branch | several production customers on different minors |
| C. **Maintenance branch on demand** | the same mechanics as B, but the branch is cut from `vX.Y.0` only when a fix must ship and `main` holds a `feat` since the tag; otherwise the patch comes from `main` as today (and is then a real patch) | the mechanics of B once; the per-minor cost only when used | the next two years: one to a few customers, security fixes a few times a year |
| D. Feature flags on `main` (ship everything, switch new features off) | every feature behind a toggle | a toggle per feature, a test matrix per toggle; the schema still moves | not for a small team; also does not stop migrations |

Recommendation: **C, built as B.** The branch is cheap to create and expensive to keep; by
creating it only when needed, most minors never get one, and the ones that do are exactly the
ones a customer runs. Concretely:

1. A fix is merged to `main` first (one truth, one review). A label `backport:0.4` on the PR
   makes Claude Code open the cherry-pick PR on `release/0.4` (the merge queue runs the full
   lane there too; the branch is protected by the same ruleset as `main`, decision gate
   included).
2. `main.yml` on a push to `release/X.Y` computes the candidate as it does on `main`, with
   the base being that line's last tag: `v0.4.1-rc.1`, `v0.4.1-rc.2`, …; images and
   pre-releases as today. The `rc` channel offers candidates of the running line only when a
   line pin is set (§4.4), so the lab host (no pin) keeps following `main`.
3. The release workflow (ADR 0079) accepts a `release/X.Y` branch for `release-pr` and `tag`
   when the version is a patch of that line; ADR 0052 point 4's check becomes "the tagged
   commit is on `main` or on `release/X.Y` and the version matches the line".
4. `CHANGELOG.md` on the branch holds the line's own section; the release PR on `main` that
   follows merges it back (the cherry-picked fixes are already in `main`'s *Unreleased*).
5. A patch release never contains a migration (§6.1), so a patch from a branch and a patch
   from `main` behave the same on the host.

What it costs per fix on a line: one cherry-pick PR (minutes when it applies cleanly; a hand
merge when it does not), one full lane (about 25 minutes), one release PR, one tag; no audit
(ADR 0078: patches skip it). What it costs once: five changes in `.github/`, `scripts/`, the
updater's channel logic and the docs (ADR 0116 steps 3 and 4).

### 5.3 Support window and the LTS idea

| Window | Who gets security fixes | Cost to us | Comparable |
|---|---|---|---|
| latest only (today) | installations on the newest minor | none | Sentry self-hosted, Keycloak (§8) |
| **`N-1`** | the newest minor and the one before | at most one maintenance branch at a time, normally none | GitLab's three minors, Grafana's two minors are stronger versions of the same idea (§8) |
| `N-2` | three minors | two branches at times | GitLab |
| LTS: a named minor kept for 12–24 months | installations that chose it | a long-lived branch with backports that diverge from `main` over time; the migration path from it grows | Mattermost ESR, Nextcloud enterprise lifetimes (§8) |

Recommendation: `N-1` for everyone, written in `SECURITY.md` and shown in the portal. A
longer window is sold per customer as an **extended line**: the same branch, kept longer, with
the backport labour and the tested upgrade path from that line paid for. Because the fix
itself is always public on `main` and on the `N-1` line, nobody is kept from a security fix;
what is paid is *not having to move*. Before 1.0 the window stays "latest only" and `N-1`
starts at 1.0, as ADR 0052 already foresaw ("fixing an older line needs its own ADR": this is
that ADR).

Cadence: today a minor every one to two weeks. With production customers, a minor every four
to eight weeks keeps the `N-1` window at two to four months of fixes, which is short. If
customers ask for a calmer rhythm, the answer is a slower minor cadence (candidates keep
flowing to the lab), not a longer free window.

## 6. Question 3: data migrations

### 6.1 Schema changes only in minors

A **patch release carries no migration**. `scripts/release_pr.py` refuses PATCH when
`migrations/versions/` changed since the last tag (it becomes MINOR, which ADR 0052's rule
"reversible migrations are MINOR" already implies, now enforced). Consequences: a patch is
always a pure image swap, its rollback needs no `alembic downgrade`, a line never diverges in
schema from its `X.Y.0`, and the upgrade matrix (§6.4) is one entry per minor.

### 6.2 Expand/contract and `N-1` compatibility

Today `web` runs `alembic upgrade head` while nothing else runs (the updater stops both
containers; the chart uses `Recreate`), so the schema never has to serve two versions at
once. That is why the current migrations may drop and rename freely. Two reasons to adopt
expand/contract anyway, now, as a rule for every new migration:

- **Downgrade becomes a restart.** If version N+1 only *adds* (tables, nullable or
  defaulted columns, new enum values) and *contracts* (drops, renames, tightened constraints)
  only one minor later, then version N runs on N+1's schema and "restore the previous version"
  does not need `alembic downgrade` any more; the data entered on N+1 stays. The downgrade path
  stays as a safety net, but it stops being the only way back.
- **The step beyond one VM** (ADR 0036) needs it, and then it must already be a habit; it is
  far cheaper to write every migration this way than to retrofit.

The rule, testable in review and partly by a check: a migration in minor N may create, add
nullable or defaulted columns, add indexes and backfill; it drops, renames or tightens only
what minor N-1 already stopped using. Persisted names (settings keys, event and job kinds)
follow the same two-step: write the new name, read both, drop the old one a minor later.

### 6.3 Skip-version upgrades (0.4 → 0.7)

Alembic runs every revision in order, so a jump is a chain; what breaks chains is a migration
that imports application code (models change under it) or depends on an external system.
Policy:

- **Migrations are self-contained**: table definitions written out in the file, no import
  from `app/` beyond pure helpers; an import-linter contract `migrations → app.adapters.db`
  forbidden makes it a CI failure.
- **A minimum upgrade-from version per release** (`MIN_UPGRADE_FROM`, baked into the image
  next to `APP_VERSION`): the version at which the oldest supported chain starts. The updater
  and the application at start refuse to migrate a database whose `running_versions` says an
  older version (`upgrade_path_required`, with the stop to go through), as GitLab and Sentry do
  with their required stops (§8). The first stop is 1.0.0; before it, every chain from 0.1 is
  kept as long as CI can test it. Raising the stop is a decision in the release PR, so a
  customer is never surprised by it.
- With the `N-1` window a supported installation is at most two minors behind; a jump across
  more minors is possible (the chain exists) but not promised and not in the matrix unless a
  customer pays for that path (§7).

### 6.4 Migration testing in CI: the upgrade matrix

What CI tests today: every migration up and down on a *fresh* database. What it does not:
the upgrade of a database that a *released* version wrote, with data in it. Proposal:

1. **A fixture per release.** The release job runs the released image once on a fresh
   database with a seed (`python -m app.cli seed --upgrade-fixture`: organisations, groups,
   schedules, slots, bookings in every status with made-up personal data, jobs pending and
   done, audit events, closures, settings in every class, an identity provider, API clients)
   and attaches `upgrade-fixture-X.Y.Z.sqlite` (and its SHA-256) to the GitHub release. It
   holds only generated data; no customer data is ever used.
2. **The matrix job** on every PR that touches `migrations/` or `app/adapters/db/`, and in
   the merge queue: for every minor inside the support window (and 1.0.0 as the stop), download
   the fixture, run `alembic upgrade head` with the PR's code, start the application, run the
   smoke tests and a set of invariants (counts per table, PII present on live bookings and
   absent on purged ones, every pending job still claimable, settings readable), then
   `alembic downgrade` to the fixture's revision and **start the released image on it** (the
   real test of "restore the previous version"), then upgrade again.
3. **Duration is measured.** The job records the migration time on a *large* fixture (a
   generated 100,000-booking database, about three years at 80 a day) and fails when it
   exceeds the budget (§6.5).

The matrix is bounded: two or three minors times one fixture. Realistic data comes from the
seed, which is maintained like a test; the sizes are settings of the seed, not of the product.

### 6.5 Long migrations

SQLite locks the whole file while a table is rebuilt (`op.batch_alter_table` copies the
table), and the updater waits `HEALTH_TIMEOUT` (300 s) for the new version. Rules:

- The schema step of a migration must finish within the budget on the large fixture; CI
  measures it (§6.4). A step that cannot is split: the schema part (expand) in the migration,
  the data part as a **background data migration**: a job kind in the existing `jobs` table
  that works in batches under the write lock, is idempotent, reports progress on the updates
  page, and must be finished before the contract step of a later minor runs (that migration
  refuses with `data_migration_pending` otherwise). This is GitLab's batched background
  migration in miniature, on our own job table, with no new infrastructure.
- Maintenance mode (ADR 0038) is on during the schema step (the updater already does this).
- The budget is a CI constant (300 s on the reference fixture), not a setting: it mirrors
  `HEALTH_TIMEOUT`, which is the one tunable value on the host.

### 6.6 Downgrade policy, backup and restore per model

| | Rule |
|---|---|
| Patch | image swap back, no schema change; the 24 h restore of ADR 0029 stays and becomes trivial |
| Minor, within 24 h | "restore the previous version": with expand/contract usually a plain restart of the previous image on the current schema; `alembic downgrade` stays the tested fallback; data entered since the update is kept |
| Minor, later | restore from a backup (data since the backup is lost) or move forward; stated plainly in the runbook |
| Major (from 1.0) | the same, plus the release notes say what cannot be undone |

Backups: the updater's backup before every switch (kept 24 h in the data volume, mode 600)
is the safety net of the update, not the backup regime of the installation. Per model:

- **Private**: the customer's regime; the runbook gives `python -m app.cli backup-db` on a
  timer and says that the file holds personal data until the purge (encrypt, keep as long as
  the retention of `docs/dataflow.md` allows, no longer).
- **Dedicated and shared**: ours: a nightly `backup-db` copied encrypted off the host or a
  volume snapshot (ADR 0114 §7), retention bounded by the purge horizon (a backup older than
  the longest hold of `app/domain/retention.py` plus 24 h brings back deleted data on restore),
  a restore tested per quarter on a second namespace, both written into the hosting contract
  and `docs/dataflow.md`.

### 6.7 Portable export/import: configuration versus full data

| Need | Answer |
|---|---|
| Move the configuration between installations (test → production, a template per customer) | the configuration document of ADR 0099 (versioned format, natural keys, no ids), once built; ADR 0089's SQLite package until then |
| Move an installation between hosting models (private → dedicated, or the other way) | the database file **is** the portable format: `backup-db`, the bootstrap keys of ADR 0042 §9 (without `SETTINGS_ENCRYPTION_KEY` every stored secret is unreadable), the `.env` of class B settings, and `alembic upgrade head` on the target; written as the runbook section *Move an installation*; offered as a paid migration service |
| A second full-data format (JSON export of bookings) | not needed and risky: it would be a second place where personal data lives; rejected |
| Import from another tool | out of scope here; a service, through the configuration document and the API |

## 7. Question 4: editions and value

### 7.1 Principle

One edition. The reasons: the licence (§7.3) makes a hosted proprietary edition by *others*
impossible but by us possible, and yet an open core would split the test matrix, the docs and
the trust of the public bodies who prefer EUPL exactly because nothing is held back; the team
is small; and the value customers ask for is operational, not functional. So the paid offer
is **work and risk**, built on the same code everyone runs:

| Offer | What it is | Why it is worth paying for | Gated? |
|---|---|---|---|
| Managed updates (dedicated, shared) | we run the rings, windows, rollbacks and restores | nobody on the customer's side touches the host | no code gate; it is hosting |
| Tested upgrade path | a named path (their line → a target) run on a copy of their database before the real update, with a report | the matrix covers `N-1` on generated data; their data and their jump are specific | service |
| Extended line | `release/X.Y` kept beyond the free window, with backports | the fix is public; the backport labour and the longer window are paid | service, public branch |
| Service level | response times, a named contact, the windows | risk transfer | contract |
| Migration service | move an installation between models, import from another tool, the configuration document per customer | one-off work | service |
| Offline bundle | the signed bundle (§4.5) and a verification procedure for their security office | CI produces it for every release; the procedure and the help are the service | the bundle itself is public like the release |
| Early access | candidates | weak value; candidates stay a lab thing | no |

What stays free, for everyone, always: the code, every release including patches (public
images, §8.4), the security fixes within the window, the updater, the installer, the chart,
the bundle, the docs and the manual. "Security fixes must stay available to everyone" is kept
by construction: they are on `main` and on the free line; what is sold is time and distance.

### 7.2 What not to do

- No "community edition" that lags the paid one, and no features that exist only in a paid
  build: the first customer is a public body, and the licence choice (ADR 0054) was made to be
  the licence such bodies prefer.
- No paid "security update subscription": it would gate what the CRA treats as an obligation
  (§7.4) and what our own `SECURITY.md` promises.
- No telemetry or licence key in the application to enforce an edition; nothing to enforce.

### 7.3 What the licence allows

EUPL-1.2 (ADR 0054) is a copyleft licence with a network clause: whoever distributes *or
provides online access to* a modified OpenPlanner must publish the modifications under
EUPL-1.2 or a compatible licence. Two facts matter for gating:

- **The licensor is not bound by its own licence.** 3Corners holds the copyright (and, through
  the contributor agreement of ADR 0054, the right to relicense contributions), so it may sell
  a commercial licence, services or support, and could in principle keep a separate
  proprietary work. The EUPL guidelines say so explicitly; see §8.6 for the sources.
- **Others may do the same with the code**, including hosting it as a service, as long as
  they publish their changes. So an "edition" built on withheld code would be the only thing
  a competitor could not copy, and everything else (hosting, support, backports) is open
  competition on quality. That is the business we are choosing with one edition, and it is the
  one the licence was chosen for.

### 7.4 The regulatory floor

The EU Cyber Resilience Act (Regulation (EU) 2024/2847) asks manufacturers of products with
digital elements to handle vulnerabilities and to provide security updates free of charge
for the support period, and treats open-source software stewards more lightly; the exact
articles and what they mean for a hosted product are in §8.6 with their sources. The point
for this note: a model in which security fixes are paid would be at odds with where the
regulation is going, besides being bad practice. The proposal does not go there.

## 8. Comparable practice

All pages were read on 2026-11-05; "unverified" marks what a page did not state or a page
that could not be fetched. No price is given unless the cited page states it.

### 8.1 Overview

| Product | Cadence | Patches from | Security-fix window | Skip versions | Downgrade | What the paid edition adds for updates |
|---|---|---|---|---|---|---|
| GitLab | minor monthly, patch twice a month, major yearly | per-minor stable branches (`18-6-stable-ee`) | current minor (bugs and security) + two previous minors (security only) | no: required stops at `x.2`, `x.5`, `x.8`, `x.11`; background migrations must finish at each | documented, but needs a database backup made under the exact version | Premium: priority support; CE and EE are one release train |
| Mattermost | monthly feature releases, major yearly, an ESR every 9 months | per-minor `release-X.Y` branches | 3 previous monthlies for high-severity security; an ESR 12 months | ESR → next ESR tested; wider jumps "supported, but not tested" | documented (`db downgrade`), not more than one major | support hours and response times; Team and Enterprise get the same security dot releases |
| Nextcloud | a major every 4 months, maintenance releases monthly | per-major `stableNN` branches | about one year per major, three majors at a time | no: one major at a time, latest point release first | "not supported and risks corrupting your data" | 5–10 years of long-term support, early security patches, upgrade stand-by, SLA |
| Grafana | minor every other month, major yearly, patches monthly | per-minor `vX.Y.x` branches, critical fixes only | 9 months per minor, 15 for the last minor of a major; every supported minor gets a CVE fix | yes, between supported releases | not documented; "Do not downgrade" (9.4) | 24×7 support; no separate update cadence |
| Sentry (self-hosted) | monthly CalVer | none: "we don't patch old versions" | latest only; out-of-cycle point release when bad enough | no: nine hard stops | not documented; restore onto the same version only | no paid self-hosted tier; SaaS features only |
| n8n | roughly weekly, patches interleaved | unverified | no published policy | "update at least once a month" | not documented; restore a backup | SLA support (Enterprise); the licence forbids hosting it as a service |
| Keycloak | 4 minors a year, a major every 2–3 years | the current minor only | latest minor only; LTS via the Red Hat build | no stated stops | not supported: restore the old server and database | no upstream paid edition |

### 8.2 Per product, with sources

**GitLab.** Monthly minor, patches twice a month, major yearly
(<https://docs.gitlab.com/policy/maintenance/>). Patches are backports onto stable branches
per minor, merged by release managers
(<https://docs.gitlab.com/development/documentation/backporting/>). Bug fixes for the current
release only; security fixes also for the previous two; lower-severity requests for older
releases "are automatically turned down" (maintenance policy page). Required upgrade stops,
with the rule that background migrations must be `Finished` before the next step
(<https://docs.gitlab.com/update/upgrade_paths/>,
<https://docs.gitlab.com/update/background_migrations/>). Zero-downtime upgrades only for
multi-node installations, one minor at a time (<https://docs.gitlab.com/update/zero_downtime/>).
Downgrade of the Linux package needs a backup made under the exact version
(<https://docs.gitlab.com/update/package/downgrade/>). The pricing page lists Premium at
"$29 per user/month, billed annually" with priority support
(<https://about.gitlab.com/pricing/>); whether security fixes reach the free edition at the
same moment is not stated on the pages read (one release train is implied): unverified.

**Mattermost.** Monthly releases; an Extended Support Release every nine months, supported
twelve months, with security and major bug fixes backported; high-severity security fixes
also to the previous three monthly releases
(<https://docs.mattermost.com/product-overview/release-policy.html>). ESR to ESR is "fully
supported and tested", wider jumps "supported, but not tested"
(<https://docs.mattermost.com/product-overview/common-esr-support-upgrade.html>). Version-specific
upgrade notes list traps such as a migration-numbering bug between two lines
(<https://docs.mattermost.com/administration-guide/upgrade/important-upgrade-notes.html>).
Downgrade is documented with the newer binary's `db downgrade`, not more than one major
(<https://docs.mattermost.com/administration-guide/upgrade/downgrading-mattermost-server.html>).
Security updates ship as the same dot releases for Team Edition and Enterprise Edition, with
details withheld for thirty days
(<https://mattermost.com/blog/mattermost-security-updates-11-7-2-esr-11-6-4-11-5-7-and-10-11-19-esr-released/>).
Paid tiers differ in support hours and response times; no prices on the page
(<https://mattermost.com/pricing/>). Per-minor `release-X.Y` branches are visible on GitHub
(<https://github.com/mattermost/mattermost/branches/all?query=release->); the branching rule
itself is inferred from the names, not from a document.

**Nextcloud.** A major every four months, each maintained about a year with monthly
maintenance releases; past end of life no fixes
(<https://github.com/nextcloud/server/wiki/Maintenance-and-Release-Schedule>). "You cannot
skip major releases"; upgrade to the latest point release first; "Downgrading is not supported
and risks corrupting your data"; fresh backup before every upgrade; the server is put into
maintenance mode (<https://docs.nextcloud.com/server/latest/admin_manual/maintenance/upgrade.html>).
The updater replaces files only; `occ upgrade` migrates the database
(<https://docs.nextcloud.com/server/latest/admin_manual/maintenance/update.html>). The
enterprise page promises "up to 5-10 years of long term support", "early security patches",
"team on standby to ensure smooth upgrades" (<https://nextcloud.com/enterprise/>); the
pricing page gives per-user prices per year at 100 users and a maintenance life cycle of one
year (Standard) versus five or more years (Premium, Ultimate)
(<https://nextcloud.com/pricing/>). Whether the community release gets a security patch later
than enterprise customers is not stated: unverified.

**Grafana.** Minor every other month, major yearly, patches at least monthly, security
releases marked with a suffix; each minor gets patches for nine months, the last minor of a
major for fifteen (<https://grafana.com/docs/grafana/latest/upgrade-guide/when-to-upgrade/>).
Backports onto `vX.Y.x` branches by a bot from a label, "reserved only for critical bug fixes"
(<https://github.com/grafana/grafana/blob/main/contribute/merge-pull-request.md>). A CVE is
fixed in every supported minor at once
(<https://grafana.com/security/security-advisories/cve-2026-100105/>). "You can upgrade to any
of the supported releases from any supported release"
(<https://grafana.com/docs/grafana/latest/upgrade-guide/>); the schema migrates at start,
with a warning that one upgrade rewrites a whole table and needs free space
(<https://grafana.com/docs/grafana/latest/upgrade-guide/upgrade-v12.0/>). Enterprise adds
support and features, no separate update cadence
(<https://grafana.com/docs/grafana/latest/introduction/grafana-enterprise/>).

**Sentry.** Monthly CalVer, "optimized for continuous deployment, *not* long-term
stability"; old versions are not patched; nine hard stops on the upgrade path
(<https://develop.sentry.dev/self-hosted/releases/>). Backup restores only onto a fresh install
of the same version (<https://develop.sentry.dev/self-hosted/backup/>). Self-hosted "comes with
no guarantees or dedicated support" and is the full product; the licence forbids selling it as
an offering (<https://develop.sentry.dev/self-hosted/>). Its CI once ran an `upgrade-test` job
that installed the previous release and then the current ref, without seeded data
(<https://github.com/getsentry/self-hosted/blob/0c82dd77fd1fb0b3d508186df083efd22cf64079/.github/workflows/test.yml>);
the current `test.yml` shows no such job: unverified whether it moved.

**n8n.** Roughly weekly releases with interleaved patches (<https://docs.n8n.io/release-notes/>);
"update at least once a month" to avoid jumping several versions
(<https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/update-n8n.md>); backup is the data
folder plus the database (<https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/backup-and-restore.md>).
The Sustainable Use License allows use "only for your own internal business purposes" and the
FAQ says one may not host n8n as a service for clients
(<https://github.com/n8n-io/n8n/blob/master/LICENSE.md>,
<https://docs.n8n.io/n8n-community-license/community-license/license-faq.md>). That is the
opposite of our licence choice and is listed as the counter-example.

**Keycloak.** Four minors a year, a major every two to three years
(<https://www.keycloak.org/2024/10/release-updates.html>). Security fixes land in the current
minor only; longer support is pointed to the Red Hat build
(<https://github.com/keycloak/keycloak/blob/main/SECURITY.md>). The database migrates
automatically at first start, or `migration-strategy=manual` writes the SQL for review;
rolling back the database "is not supported": restore the old server and its backup
(<https://www.keycloak.org/docs/latest/upgrading/index.html>). The Red Hat build's support
window (two minors, about twelve months of patches) comes from a search snippet only:
unverified.

### 8.3 What we take from it

- **Every product with a support window longer than "latest" uses a branch per line and
  backports by label and bot** (GitLab, Mattermost, Grafana, Nextcloud). The lazy variant
  (§5.2 option C) is our own economy; the mechanics are the industry's.
- **Security fixes reach the free edition in the same release** where a page says anything
  (Mattermost explicitly, GitLab and Grafana by one release train). Nextcloud's "early security
  patches" for enterprise is the one counter-signal, and it is the one we should not copy.
- **What is sold is time**: a longer life cycle (Nextcloud 5–10 years, Mattermost ESR, Red
  Hat's Keycloak), support hours and response times, and upgrade stand-by. Nobody sells the
  fix.
- **Required stops are normal** (GitLab, Sentry, Nextcloud one major at a time) and so is
  "jumps are possible but untested" (Mattermost). A declared minimum upgrade-from version
  (§6.3) is the small-product version of that.
- **Downgrade is rarely supported**; those who support it (Mattermost) require a backup and
  bound it to one major. Our 24 h restore with reversible migrations is already stronger than
  most; expand/contract makes it cheap to keep.
- **Long migrations are the known pain** (GitLab's batched background migrations, Mattermost's
  JSONB migration of eleven minutes, Grafana's table rewrite); a budget measured in CI (§6.5)
  is cheaper than any of those.
- **Nobody with a fast cadence promises stability** (Sentry, n8n). If customers need calm,
  the cadence of *releases* slows; candidates can keep flowing.

### 8.4 Public patch images (amending ADR 0108)

ADR 0108 publishes `vX.Y.0` only; it names the consequence itself: "a security fix shipped as
0.4.1 is not in the public package until 0.5.0", and offers the alternative "`vX.Y.Z`
releases, not candidates" as a new ADR. Every comparable product publishes its patch builds.
The proposal takes that alternative: every release `vX.Y.Z` goes to the public packages,
candidates stay private. The release audit (ADR 0078) still runs only for `vX.Y.0`; a patch
carries fixes only (no migration, §6.1) and is reviewed like any PR.

### 8.5 Migration practice, sources

- Expand/contract: Fowler's *ParallelChange* (expand, migrate, contract; skipping contract
  leaves "a worse state than you started"), <https://martinfowler.com/bliki/ParallelChange.html>;
  GitLab's *Avoiding downtime in migrations* applies it across releases (ignore the column in
  release M, drop it in M+1, remove the ignore in M+2),
  <https://docs.gitlab.com/development/database/avoiding_downtime_in_migrations/>.
- Alembic: branches and `alembic merge` (<https://alembic.sqlalchemy.org/en/latest/branches.html>),
  batch mode for SQLite's limited `ALTER` (move-and-copy of the table; unnamed constraints
  need a naming convention; `PRAGMA foreign_keys` off while a referenced table is rebuilt),
  <https://alembic.sqlalchemy.org/en/latest/batch.html>. The repository already uses a linear
  chain and reversible migrations; the matrix of §6.4 adds the test from released data.
- Batched background migrations and required stops: GitLab,
  <https://docs.gitlab.com/development/database/batched_background_migrations/> ("waiting for
  the next release is not enough to guarantee that the data was fully migrated"; a later
  migration must assert that it finished), the model for §6.5.
- Upgrade tests in CI: Sentry's earlier `upgrade-test` job (install the previous release, then
  the current ref), see §8.2; Django's `makemigrations --check` fails CI on model changes
  without a migration (<https://docs.djangoproject.com/en/5.2/ref/django-admin/>), which
  Alembic's `check` command gives us already in `make fast`. Examples with seeded data from
  the other products were not found: unverified.

### 8.6 Licence and regulation, sources

- EUPL-1.2 text, article 2 (a "worldwide, royalty-free, non-exclusive, sublicensable"
  licence) and article 1 (communication to the public counts as distribution):
  <https://interoperable-europe.ec.europa.eu/sites/default/files/custom-page/attachment/2020-03/EUPL-1.2%20EN.txt>.
- *How to use the EUPL*: the licensor keeps the copyright and "may even manage two simultaneous
  distributions"; services around the software are not restricted:
  <https://interoperable-europe.ec.europa.eu/collection/eupl/how-use-eupl>.
- EUPL FAQ: selling the code, installation, training, customisation, warranty and support is
  allowed; per-user royalties do not fit the licence; what makes a work derivative, and the
  "SaaS loophole" closed by article 1 as the AGPL does:
  <https://interoperable-europe.ec.europa.eu/collection/eupl/faqs>. The 2021 EUPL guidelines
  PDF could not be fetched: unverified.
- AGPL-3.0 §13 (offer the source to remote users) and §5 (aggregates), for the comparison:
  <https://opensource.org/license/agpl-v3>.
- Cyber Resilience Act, Regulation (EU) 2024/2847. EUR-Lex returned empty pages to every fetch
  on 2026-11-05, so the wording below comes from mirrors and is **unverified against the
  Official Journal**: article 13(8) support period of at least five years; 13(9) each security
  update available for at least ten years or the rest of the support period; 13(10) the last
  version free of charge to users of earlier versions
  (<https://www.springlex.eu/en/packages/cra/cra-regulation/article-13/>); Annex I part II:
  remediate vulnerabilities "without delay, including by providing security updates", where
  feasible "separately from functionality updates", disseminated "free of charge"
  (<https://docs.modulos.ai/frameworks/cra/vulnerability-handling-and-support-period>);
  article 24 on open-source software stewards (a documented cybersecurity policy, cooperation
  with market surveillance) (<https://www.springlex.eu/en/packages/cra/cra-regulation/article-24/>).
  Whether OpenPlanner's hosted models make 3Corners a manufacturer under the CRA is a question
  for a lawyer, not for this note; the proposal is consistent with either answer.
- An OSI statement on gating security fixes was looked for and not found: unverified.

## 9. Assumptions

1. The three models are run at the sizes of ADR 0036 (one writer, one file per installation);
   the shared model is one or more cells of that shape. Horizontal scaling is ADR 0036's step
   and changes nothing here except that expand/contract then becomes mandatory.
2. Before 1.0 the window stays "latest only"; the mechanics are built and exercised once on
   `release/0.4` (or the current line at that time) as a dry run.
3. The cadence after the first production customer is a minor every four to eight weeks.
4. Backports are made by Claude Code from a label and reviewed like any PR; a cherry-pick that
   does not apply cleanly is resolved by hand in the same PR.
5. The offline bundle's verification works with the Sigstore tooling we already use; to be
   proven on a disconnected VM before it is promised.
6. GitHub remains the forge; every mechanism is a script in `scripts/` with a thin workflow,
   as ADR 0079 requires, so an internal forge can run it.

## 10. Open questions for the product owner

The three decisions this note needs:

1. **Support window and public patches.** `N-1` for everyone, longer only as a paid extended
   line; and every `vX.Y.Z` release public (ADR 0108 amended), candidates private. Yes or
   another window?
2. **When to start maintenance lines.** Build the mechanics now and exercise them once with a
   `release/0.4` dry run while 0.5 is in progress, or wait until 1.0 and keep "latest only"
   until then?
3. **Commercial model.** One edition, services sold (managed updates, tested upgrade path,
   extended line, service level, migration service), no open core. Yes?

Further questions, not blocking:

4. May production installations auto-apply inside a window (relaxing ADR 0067 point 1), or
   must a person always click in production?
5. Is a 100,000-booking fixture the right reference size for the migration budget?
6. For the shared model: one cell with many organisations, or a cell per customer? (The
   authorisation matrix supports the first; backups, windows and restore are simpler per
   customer in the second.)
7. Who signs the hosting contract's backup retention, given that backups hold personal data
   beyond the purge horizon?

## 11. What to measure

| Measure | Why | Where |
|---|---|---|
| Time from merge of a security fix to its release on each supported line | the same-day promise of ADR 0052 point 6, per line | the release workflow's runs |
| Time from release to "running" per ring | whether the windows and rings work | `running_versions` and the audit log per installation |
| Updates that rolled back, and why | the quality of the health check and of migrations | `update_requests` outcomes |
| Migration duration on the large fixture per release | the budget of §6.5 | the matrix job |
| Installations per line, and outside the window | whether `N-1` is enough | the support notice in the portal is per installation; a central count only for the installations we host |
| Cherry-picks that did not apply cleanly | the real cost of lines | the backport PRs |
| Restores tested per quarter (hosted models) | the backup promise | the hosting runbook |

## 12. Follow-up steps

In the order they can be built; each is a build step of ADR 0116 unless it says decision.

1. **Decision**: ADR 0116 accepted; `SECURITY.md` states the window; ADR 0052 point 6 and
   ADR 0108 point 1 amended; ADR 0114 point 11 withdrawn in favour of the line pin.
2. **Rules in code**: `release_pr.py` refuses PATCH with a migration; import-linter contract
   for self-contained migrations; the expand/contract rule in CLAUDE.md and the PR template.
3. **Upgrade fixture and matrix**: `seed --upgrade-fixture`, the fixture attached to every
   release, the matrix job with the downgrade-and-start-the-old-image check and the duration
   budget.
4. **Lines**: candidates on `release/X.Y`, the release workflow on a line, the tag check, the
   ruleset for `release/*`, the `backport:X.Y` label and the cherry-pick PR; one dry run.
5. **Portal and updater**: `update_line`, `update_window` with `apply_after`, the support
   notice from the signed index, `MIN_UPGRADE_FROM` check; manual topic *Updates* updated.
6. **Public patch images**: `main.yml` pushes every `vX.Y.Z` to the public packages.
7. **Offline bundle**: `release-bundle` job, `install.sh --bundle`, `UPDATE_SOURCE` and
   `UPDATE_INDEX_URL`, offline verification proven on a disconnected VM, runbook section.
8. **Background data migrations**: the job kind, progress on the updates page, the
   `data_migration_pending` refusal.
9. **Docs and contracts**: runbook sections *Move an installation*, *Backups per model*,
   *Rings and windows*; `docs/dataflow.md` (backups); the editions and support table on the
   README and the website; the Kubernetes admission policy in `docs/kubernetes.md`.
