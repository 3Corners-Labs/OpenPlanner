---
name: security-review
description: Run before opening any PR in this repo. Answers the five review questions, walks the abuse cases, and checks the PII and authorisation rules from CLAUDE.md against the diff. Use when asked to "review this PR", "security check", or before any `gh pr create`.
---

# Security review (repo skill)

Review the diff of the current branch against `main` (`git diff main...HEAD`, plus uncommitted
changes). Read every changed file in full, not only the hunks. Report findings; do not fix them
in this step.

## 1. Five review questions

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

## 2. Rule checks

- `app/domain/` imports nothing from frameworks or adapters.
- No raw SQL outside `app/adapters/db/`.
- Datetimes are timezone-aware UTC; conversion only at edges.
- Booking/slot writes use the write lock; cancel/reschedule deletes pending jobs in the same
  transaction; time-based jobs check `pinned_start`.
- Management tokens: `secrets.token_urlsafe(32)`, stored as sha256, compared with
  `hmac.compare_digest`.
- Webhooks: HMAC signature verified, timestamp window 300 s.
- New user-facing strings use `_()` with nl and en entries.
- Migrations have a working `downgrade()`; merged migrations untouched.
- No `print`, no `!important`, no inline styles in widget code.

## 3. Abuse cases

Walk `docs/threat-model.md` for every touched flow. For each abuse case (slot exhaustion by
bots, token guessing, rescheduling into another group's slot, PII through error messages, and
any listed there) state whether this diff affects it and which test covers it. If the diff adds
a flow or endpoint, check that the threat model and `docs/dataflow.md` were updated.

## Output

A Markdown section ready to paste into the PR description:

```
### Security review
**Q1–Q5:** ...
**Rule checks:** pass / findings
**Abuse cases:** ...
**Findings:** numbered, with severity (high/medium/low) and file:line
```
