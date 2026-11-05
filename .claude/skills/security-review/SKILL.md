---
name: security-review
description: Security review of the current branch against main. Answers the eight review questions, checks the PII, authorisation and configurability rules from CLAUDE.md, the change's impact on developer setup and feature gates, and walks the abuse cases. `pre-pr-review` runs it before every PR; use it on its own for a "security check" or "security review".
---

# Security review (repo skill)

Review the diff of the current branch against `main` (`git diff main...HEAD`, plus uncommitted
changes). Read every changed file in full, not only the hunks. Report findings; do not fix them
in this step.

Trace a finding end to end before you report it. When the other side of it (the caller, the
template, the job, the adapter) is in the repository, read it instead of writing "this is fine if
X handles it". Rate severity honestly: an inflated high costs the review its trust.

## 1. Eight review questions

Answer each with file:line references. "None" is a valid answer only after checking.

1. Which endpoint or job is new or changed, and does every admin route depend on exactly one
   `RequirePermission(Permission.…)` (the `require_scope` of CLAUDE.md)? Public endpoints
   (`/g/...`, `/api/v1/...`, `/b/{token}`, health) are exempt from it but must be rate-limited
   where `CLAUDE.md` says so.
2. Which input comes from outside (HTTP body, query, path, headers, webhook, IdP claims, env),
   and where is it validated (Pydantic model, domain check)?
3. Can PII (the fields of `DATA_FIELDS` in `app/domain/privacy.py`) or a provider link from
   `DERIVED_FIELDS` reach a log call, exception message, URL, job payload, audit event or
   outbound payload? An outbound payload to the contact center carries only what CLAUDE.md's
   privacy rules list, and `app/adapters/wxcc.py` is the one place that builds it.
4. Which test would fail if this change were wrong? Name it. If none, that is a finding.
5. Is a dependency added? Check license (MIT/BSD/Apache-2.0/PSF, else ADR), maintenance, that
   `uv.lock` changed accordingly and that `docs/dependencies.md` records the review.
6. Which settings does this add or change, and which tunable value stays hard-coded? See
   section 3 for the check; answer with a table: key, class A–D, default, bounds, permission.

7. Which admin screen or setting is new or moved, and where does it live (scope → object → tab,
   ADR 0047)? Check against the diff:
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
8. Does this change name a client or a sector anywhere: code, tests, docs, texts, commit
   messages or the branch name (ADR 0115)? `tests/test_terminology.py` reads tracked files
   only, so stage new files before you run it.

## 2. Rule checks

- `app/domain/` imports nothing from frameworks or adapters.
- No raw SQL outside `app/adapters/db/`.
- Datetimes are timezone-aware UTC; conversion only at edges.
- Booking/slot writes use the write lock; cancel/reschedule deletes pending jobs in the same
  transaction; time-based jobs check `pinned_start`.
- Management tokens (ADR 0011): HMAC-SHA256 over a 32-byte random per-booking salt under
  `MANAGEMENT_TOKEN_KEY`; only the salt and `sha256(token)` stored; compared with
  `hmac.compare_digest`; never in a job payload or log.
- POST forms on public pages depend on `require_same_origin` and a rate limit.
- Webhooks: HMAC signature verified, timestamp window 300 s.
- New user-facing strings use `_()` with nl and en entries.
- Migrations have a working `downgrade()`; merged migrations untouched.
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
  sizes, PII field names, crypto parameters, the 300 s webhook window and protocol constants.
  The PR still names them under question 6.

## 4. Change impact

- **Developer and host setup.** A new, renamed or removed environment variable, a changed port or
  service in `docker-compose.yml`, or a new step someone must take to run or deploy (a script, a
  manual install, a new secret): check that `.env.example`, the installer, `docs/runbook.md` and
  the generated `docs/settings.md` follow, and that the PR says what changes for a running
  deployment (finding: medium). A dependency added through `uv` is not a setup change.
- **Gate leaks.** Behaviour meant to sit behind a permission, an entitlement (ADR 0049), a
  setting or kill switch, a superuser-only mode or a test-only path must not be reachable without
  it. Name the gate for each new route, job and template branch (finding: high when gated
  behaviour leaks).
- **Intended breakage.** A change that deliberately removes a safeguard or changes behaviour is
  not a finding when its scope is tight and the PR says so. Report it anyway when the PR seems
  unaware of the full effect.

## 5. Abuse cases

Walk `docs/threat-model.md` for every touched flow. For each abuse case (slot exhaustion by
bots, token guessing, rescheduling into another group's slot, PII through error messages, and
any listed there) state whether this diff affects it and which test covers it. If the diff adds
a flow or endpoint, check that the threat model and `docs/dataflow.md` were updated.

## Output

A Markdown section ready to paste into the PR description:

```
### Security review
**Q1–Q8:** ...
**Settings:** table, or "none" after checking
**Rule checks:** pass / findings
**Change impact:** setup, gates, intended breakage, or "none" after checking
**Abuse cases:** ...
**Findings:** numbered, with severity (high/medium/low) and file:line
```
