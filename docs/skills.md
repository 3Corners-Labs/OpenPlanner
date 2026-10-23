# Claude Code skills per work package

Which skill to use when. "Repo" means `.claude/skills/`; the others come from gstack or the
engineering plugin. Claude Code names the skill it is about to use. Skills that merge or deploy
are excluded: Claude Code merges only on an explicit instruction of the product owner, with
green CI, and never through a skill (ADR 0012).

## Every work package

| Moment | Skill | Why |
|---|---|---|
| Start | `/context-restore` | Pick up where the previous session stopped |
| Before coding | `/plan-eng-review` | Check the design against the architecture rules before writing code |
| While coding | `/freeze app/<module>` (optional) | Keep edits inside the module being built |
| Bug or failing test | `/investigate` | Find the root cause before fixing |
| Before the PR | `security-review` (repo), then `/review` | Five review questions and abuse cases; then a pre-landing diff review |
| End | `/context-save` | Hand over to the next session |

## Per work package

| WP | Topic | Skills |
|---|---|---|
| 2 | Domain | `engineering:testing-strategy` (test plan for slot calculation and locking), `/codex` (second opinion on the domain model), `engineering:architecture` (ADR for the lock strategy) |
| 3 | Public API, widget, i18n | `/plan-design-review`, `/design-review` (NLDS tokens, focus, contrast), `/qa-only` or `/qa` with `/browse` (keyboard-only flow), `/cso` (abuse cases for public endpoints) |
| 4 | Mail, .ics, jobs | `engineering:testing-strategy` (pinning and job races), `/investigate` |
| 5 | Webex video | `engineering:architecture` (ADR after O1: Instant Connect or Meetings API), `/cso` |
| 6 | Webex Connect | `/cso` (HMAC, replay window, SSRF), `engineering:documentation` (`docs/webhooks.md`, Connect flow) |
| 7 | Admin UI, OIDC, translation editor | `/cso` (OIDC, authorisation matrix), `/plan-design-review`, `/design-review`, `/qa` |
| 8 | PII purge, audit | `/cso`, `engineering:documentation` (`docs/dataflow.md` for the DPIA) |
| 9 | Hardening | `/cso` (full audit), `/health`, `/benchmark`, `engineering:deploy-checklist`, `engineering:documentation` (runbook), `/document-release` |

## Not used

- `/land-and-deploy`, `/ship`: they merge or push past the PR. We stop at an opened PR.
- `/design-consultation`, `/design-shotgun`: the design system is fixed (NLDS, HANDOVER B12).
