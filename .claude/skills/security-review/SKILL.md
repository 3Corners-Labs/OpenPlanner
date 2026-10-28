---
name: security-review
description: Run before opening any PR in this repo. Answers the six review questions, walks the abuse cases, and checks the PII, authorisation and configurability rules from CLAUDE.md against the diff. Use when asked to "review this PR", "security check", or before any `gh pr create`.
---

# Security review (repo skill)

Review the diff of the current branch against `main` (`git diff main...HEAD`, plus uncommitted
changes). Read every changed file in full, not only the hunks. Report findings; do not fix them
in this step.

## 1. Six review questions

Answer each with file:line references. "None" is a valid answer only after checking.

1. Which endpoint or job is new or changed, and is `require_scope` on it? Public endpoints
   (`/g/...`, `/api/v1/...`, `/b/{token}`, health) are exempt from `require_scope` but must be
   rate-limited where `CLAUDE.md` says so.
2. Which input comes from outside (HTTP body, query, path, headers, webhook, IdP claims, env),
   and where is it validated (Pydantic model, domain check)?
3. Can PII (`name, email, phone, note, guest_join_url, host_join_url`) reach a log call,
   exception message, URL, job payload, audit event or outbound payload? Outbound to WxCC may
   carry only first name and host join URL.
4. Which test would fail if this change were wrong? Name it. If none, that is a finding.
5. Is a dependency added? Check license (MIT/BSD/Apache-2.0/PSF, else ADR), maintenance, and
   that `uv.lock` changed accordingly.
6. Which settings does this add or change, and which tunable value stays hard-coded? See
   section 3 for the check; answer with a table: key, class A–D, default, bounds, permission.

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

## 4. Abuse cases

Walk `docs/threat-model.md` for every touched flow. For each abuse case (slot exhaustion by
bots, token guessing, rescheduling into another group's slot, PII through error messages, and
any listed there) state whether this diff affects it and which test covers it. If the diff adds
a flow or endpoint, check that the threat model and `docs/dataflow.md` were updated.

## Output

A Markdown section ready to paste into the PR description:

```
### Security review
**Q1–Q6:** ...
**Settings:** table, or "none" after checking
**Rule checks:** pass / findings
**Abuse cases:** ...
**Findings:** numbered, with severity (high/medium/low) and file:line
```
