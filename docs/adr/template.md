# NNNN. Title in plain words

Status: proposed (YYYY-MM-DD), requested by … Extends / amends / supersedes ADR …

<!--
Copy this file to docs/adr/NNNN-title.md. Before choosing NNNN, check origin/main and the
open PRs: parallel branches often claim the same number. ADRs are never rewritten after
merge; a changed decision gets a new ADR and the old one is marked "superseded by NNNN".
-->

## Context

What problem or need, for whom, and what constrains the answer.

## Decision

Numbered points. Each one should be testable.

## Settings

<!--
Required (CLAUDE.md, "Configurable by default"; ADR 0042). List every threshold, limit,
period, interval, toggle or external address that this decision introduces or changes. Write
"None" only after checking.
-->

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `example_retention_days` | C platform | 90 | 30–3650 | `settings.platform.manage` | |

Classes:

- **A, bootstrap (env):** needed before the database or the login works.
- **B, deployment (env):** tied to the host, or a kill switch.
- **C, platform:** set by the superuser.
- **D, organisation:** set by the org admin.

A and B need a reason. A protection can never be switched off from the portal.

**Fixed on purpose:** values that stay constants, and why (for example WCAG target sizes or
crypto parameters).

## Consequences

What becomes easier or harder: docs, threat model, data flow, manual, migrations,
dependencies.
