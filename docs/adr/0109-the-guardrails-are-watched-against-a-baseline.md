# 0109. The guardrails are watched against a baseline in the repository

Status: accepted (2026-11-02, `po:approved` on #454), requested by the product owner. Builds on
ADR 0078, ADR 0104 and ADR 0107: they set the guardrails, this ADR notices when one changes or
is passed. Partly built; see *Build status*.

## Context

On 2026-11-02 the repository got its guardrails in GitHub's settings, not in code: a ruleset on
`main` without bypass, a tag ruleset whose only bypass is the release app, environments that
only `main` may use, squash-only merges, secret scanning with push protection, read-only
workflow tokens. Settings can be changed by an administrator in a few clicks, an app can be
installed on every repository, and a ruleset can be passed by whoever is on its bypass list.
GitHub records this in the organisation's audit log, but on the Team plan nothing tells the
product owner, and the audit log API is an Enterprise feature.

A probe with the workflow's own token (#450) showed what it can read: rulesets and their rules,
environments and their branch policies. It cannot read who may bypass a ruleset, whether anyone
did (rule suites), the repository's merge and security settings, or the Actions permissions.
Those need *Administration: read*.

## Decision

1. A GitHub App of the organisation, the *audit app*, with *Administration: read* on the
   repository and on the organisation and nothing else, installed on this repository only. It
   cannot change anything.
2. Its client ID (variable `AUDIT_APP_CLIENT_ID`) and private key (secret
   `AUDIT_APP_PRIVATE_KEY`) live in the environment `audit`, whose deployment branches are
   `main` only.
3. The expected state is a file in the repository, `.github/guardrails.json`: the rulesets
   (target, enforcement, conditions, rules with their parameters, bypass actors), the
   repository's merge and security settings, the Actions token defaults, the environments and
   their branch policies, the users with more than read access and their role, the
   organisation's two-factor requirement and repository-creation setting, the installed apps
   with their repository selection and permissions, and the bypasses that are expected (actor
   and ref prefix: the release app on `refs/tags/v`). Because it lives under `.github/`, every
   change to it is a decision (ADR 0078).
4. `.github/workflows/guardrail-audit.yml` runs every hour, after a merge that changes the
   baseline, and on demand. It reads the live state through the API with the audit app's token
   and passes it as files to `scripts/guardrail_audit.py`, which compares and makes no network
   call. Anything that differs, anything that cannot be read, and every bypass in the last day
   that the baseline does not expect, goes into one report.
5. The report is the one open issue `Guardrail drift <date>` (label `guardrail-drift`),
   created, updated in place and closed when nothing drifts, with the workflow's own token.
   GitHub mails the product owner when it opens. The logic of keeping one open report issue is
   shared with the ADR drift report (`scripts/report_issue.py`, ADR 0005).
6. The workflow fails closed: when the app or its key is missing, the report says so.

## Settings

No application setting. The workflow reads two values from the forge:

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `AUDIT_APP_CLIENT_ID` | A bootstrap (environment variable `audit`) | none | the audit app's client ID | repository admin | Not secret. |
| `AUDIT_APP_PRIVATE_KEY` | A bootstrap (environment secret `audit`) | none | the audit app's private key (PEM) | repository admin | The app can only read. |

**Fixed on purpose:** the hourly schedule and the one-day window for bypasses (a bypass is
seen within an hour and stays in the report for a day; the issue mail is the alarm), the issue
title and label, and the app's permissions. They are process constants of the forge.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; environment `audit` created with branch policy `main` (2026-11-02) | #454 | in PR |
| 1 | The product owner creates the audit app, installs it on this repository and stores its client ID and key in the environment `audit` | GitHub setting | planned |
| 2 | `.github/guardrails.json`, `scripts/guardrail_audit.py`, `scripts/report_issue.py` (shared with `scripts/adr_issues.py`), `.github/workflows/guardrail-audit.yml`; tests; runbook, threat model | #454 | in PR |
| 3 | The first scheduled run after the merge reads every section; anything it cannot read is fixed in the workflow or the baseline | | planned |

## Consequences

- A change to a guardrail, a new administrator, a new app on the organisation or a bypass by
  anyone but the release app reaches the product owner within an hour, by mail.
- Changing a guardrail on purpose now takes two steps: the setting, and the baseline in a
  decision PR. Until the baseline follows, the issue stays open: that is the point.
- The organisation's two-factor requirement is in the baseline as required; until the product
  owner sets it, the first report shows it.
- One more app key to keep; it can only read, and rotating it is the same as for the release
  app (runbook).
