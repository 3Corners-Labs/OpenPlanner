---
name: security-review
description: Security review of a change in this repo. Answers the review questions of the PR template, checks the PII, authorisation, safeguard and configurability rules from CLAUDE.md and the change's impact on setup and feature gates, and walks the abuse cases. `pre-pr-review` runs it before every PR; use it on its own for a "security check" or "security review".
---

# Security review (repo skill)

Review the scope you are handed; on your own, take the scope of `pre-pr-review` §1. Its
read-only rule holds either way. Read every changed file in full, not only the hunks. Report
findings; do not fix them.

Trace a finding end to end before you report it. When the other side of it (the caller, the
template, the job, the adapter) is in the repository, read it instead of writing "this is fine if
X handles it". Rate severity honestly: an inflated high costs the review its trust.

## 1. Review questions

Answer every question of `.github/pull_request_template.md` in its own wording, with file:line
references. "None" is a valid answer only after checking. What to check for each:

1. Every admin route depends on exactly one `RequirePermission(Permission.…)`. Public endpoints
   (`/g/...`, `/api/v1/...`, `/b/{token}`, health) are exempt from it but must be rate-limited
   where `CLAUDE.md` says so.
2. Input from outside (HTTP body, query, path, headers, webhook, IdP claims, env) and where it
   is validated (Pydantic model, domain check).
3. PII is any name in `PII_FIELDS` of `app/logging.py`: the fields of `DATA_FIELDS`, the other
   names that data travels by, and the staff user name. Check log calls, exception messages,
   URLs, job payloads, audit events and outbound payloads. The fields of `DERIVED_FIELDS`
   (`app/domain/privacy.py`) are not PII on their own and are not masked, but they find the
   participant again at the provider: prefer the booking id in a log line, name every new log line
   that carries one of them in this answer, and add a new such field to that list so that the
   purge deletes it. An outbound payload to the contact
   center carries only what CLAUDE.md's privacy rules list, and `app/adapters/wxcc.py` is the one
   place that builds it.
4. The test that would fail if this change were wrong. If there is none, that is a finding.
5. A new dependency: why it is needed, its licence (MIT/BSD/Apache-2.0/PSF, else ADR), its
   maintenance, `uv.lock` changed accordingly, and the review recorded in `docs/dependencies.md`.
6. The table from section 3 (key, class A–D, default, bounds, permission), and for every
   tunable value that stays hard-coded, why.
7. Where a new or moved screen or setting lives (scope → object → tab, ADR 0047) and why it fits
   there. Check against the diff:
   - every new `GET` route under `/admin` that returns HTML is a register entry, tab or sub-tab in
     `app/web/navigation.py`, or a declared sub-page of one;
   - a new section, top-level entry or seventh tab on an object comes with an ADR (finding:
     medium if missing);
   - a new setting has a `topic` on its own scope (class C → platform, D → organisation) and sits
     next to settings of the same kind;
   - no second home: a page that asks "which organisation?" or "which group?" before an edit form
     is a finding (medium); cross-object pages are lists that link to the object's tab;
   - a `return` key or legacy redirect resolves only through the register, never to a URL taken
     from the request (high if it does).
8. Client or sector names (ADR 0115): read the diff, the untracked new files, the commit
   messages and the branch name against the patterns in `tests/test_terminology.py`. That test
   reads tracked files only: when there are untracked new files, tell the author to stage them and
   run it.

## 2. Rule checks

CI already gates the layer imports (`lint-imports`) and running migrations up and down; check
the rules it does not.

- No raw SQL outside `app/adapters/db/`.
- Datetimes are timezone-aware UTC; conversion only at edges.
- Booking/slot writes use the write lock; cancel/reschedule deletes pending jobs in the same
  transaction; time-based jobs check `pinned_start`.
- Management tokens (ADR 0011): HMAC-SHA256 over a 32-byte random per-booking salt under
  `MANAGEMENT_TOKEN_KEY`; only the salt and `sha256(token)` stored; compared with
  `hmac.compare_digest`; never in a job payload or log.
- POST forms on public pages depend on `require_same_origin` and a rate limit.
- Webhooks: HMAC signature verified, timestamp window 300 s.
- The safeguards of CLAUDE.md's security baseline stay in place, among them: the honeypot on
  the booking form, CSP, HSTS and `X-Frame-Options: DENY` on every response, the CORS allow-list,
  the OIDC checks (PKCE, `state`, `nonce`, asymmetric algorithms only), the session cookie flags,
  no login bypass or role switcher, integration secrets stored encrypted, write-only and audited
  (ADR 0008), and the PII mask in `app/logging.py`.
- New user-facing strings use `_()` with nl and en entries.
- Merged migrations are untouched. CI runs every migration up and down, but its seed data does
  not exercise a later migration's data move, so a migration that moves data has its own
  up-and-down test with data in `tests/test_migrations.py`.
- No `print`, no `!important`, no inline styles in widget code.

## 3. Settings check (configurable by default, ADR 0042)

Scan the diff for tunable values and classify each one:

- **What counts:** new fields in `app/config.py`; new `os.environ`/`getenv` reads or
  `BaseSettings` classes (only `app/config.py` may read the environment); new module-level
  constants or literals in `app/` that set a threshold, limit, period, interval, retry count,
  size cap, toggle or external URL (`MAX_*`, `*_SECONDS`, `*_DAYS`, `*_LIMIT`, `timedelta(...)`,
  rate strings such as `"10/hour"`); new fields on a group or organisation that only a
  developer can set.
- **Classify** each as A bootstrap, B deployment, C platform or D organisation (CLAUDE.md,
  ADR 0042). A or B needs one of the reasons listed there; a kill switch that turns a
  protection off must be B.
- **Findings:**
  - a new env variable or constant that could be C or D, with no reason in the PR (medium);
  - a C or D setting without a default, bounds or a permission (medium);
  - a protection that can be switched off from the portal (high);
  - an ADR in the diff without a *Settings* section (low).
- **Fixed on purpose:** values that are fixed rules are not findings, for example WCAG target
  sizes, PII field names, crypto parameters, the 300 s webhook window and protocol limits such as
  the 254-character address of RFC 5321. The PR still names them under question 6. This list is
  the one home of the fixed rules; `code-quality-review` refers to it.

## 4. Change impact

- **Developer and host setup.** A new, renamed or removed environment variable, a changed port or
  service in `docker-compose.yml`, or a new step someone must take to run or deploy (a script, a
  manual install, a new secret): check that `.env.example`, `proxy.env.example`,
  `scripts/updater/updater.env.example`, the installer `scripts/install.sh`, the Helm chart
  (`env` and `secretKeys` in `deploy/helm/openplanner/values.yaml`), `docs/install.md`,
  `docs/kubernetes.md`, `docs/runbook.md` and the generated `docs/settings.md` follow, and that
  the PR says what changes for a running deployment (finding: medium). A dependency added
  through `uv` is not a setup change.
- **Gate leaks.** Behaviour meant to sit behind a permission, an entitlement (ADR 0049), a
  setting or kill switch, a superuser-only mode or a test-only path must not be reachable without
  it. Name the gate for each new route, job and template branch (finding: high when gated
  behaviour leaks).
- **Safeguards.** A change that removes or weakens a safeguard (anything CLAUDE.md, an ADR or
  section 2 requires) is always a finding: name the ADR that decides it, and rate it high when
  there is none. An ADR that this PR adds counts, because the decision gate holds such a PR for
  the product owner. A behaviour change that removes no safeguard is not a finding when its scope
  is tight and the commit message says so.

## 5. Abuse cases

Walk `docs/threat-model.md` for every touched flow. For each abuse case (slot exhaustion by
bots, token guessing, rescheduling into another group's slot, PII through error messages, and
any listed there) state whether this diff affects it and which test covers it. If the diff adds
a flow or endpoint, check that the threat model and `docs/dataflow.md` were updated.

## Output

Two blocks for the PR description, under the template's own headings:

```
## Review questions
one answer per question of the template, with file:line

## Security review
**Settings:** table, or "none" after checking
**Rule checks:** pass / findings
**Change impact:** setup, gates, safeguards, or "none" after checking
**Abuse cases:** ...
**Findings:** numbered, with severity (high/medium/low) and file:line
```
