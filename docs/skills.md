# Claude Code skills per work package

Which skill to use when. "Repo" means `.claude/skills/`; the others come from gstack, the
engineering plugin or Claude Code itself. Claude Code names the skill it is about to use. Skills
that merge or deploy are excluded: a pull request merges through the merge queue, build work on
green CI and a decision with the product owner's label (ADR 0078), never through a skill.

## Every work package

| Moment | Skill | Why |
|---|---|---|
| Start | `/context-restore` | Pick up where the previous session stopped |
| Before coding | `/plan-eng-review` | Check the design against the architecture rules before writing code |
| Before coding, and before changing a shared symbol | Graphify MCP (`graphify_find`, `graphify_impact` with `include_risk=true`, `graphify_tests_for`) | Find the definition, the callers, the blast radius and the linked tests from the code graph instead of grepping; CLAUDE.md, *How to work in this repo*, says when to read the file instead |
| While coding | `/freeze app/<module>` (optional) | Keep edits inside the module being built |
| Bug or failing test | `/investigate` | Find the root cause before fixing |
| Before the PR | `pre-pr-review` (repo) | The review CLAUDE.md asks for before every PR, in one command; see *Repo skills* |
| End | `/context-save` | Hand over to the next session |
| Release of a minor or major version | `/codex review` via `make release-audit` | Independent review of everything since the last release by a second model, pasted into the release PR (ADR 0078). Since ADR 0079 the `audit` job of the `release` workflow runs it and writes the section; the laptop form is the fallback (`docs/runbook.md`, *Releasing a version*) |

## Repo skills

One skill per review concern, one skill that runs them together, and one skill per recurring
kind of work outside the code.

| Skill | What it does |
|---|---|
| `pre-pr-review` | Runs the two skills below in parallel subagents and `/code-review` (level `high`) in the main thread, checks every finding against the code, deduplicates, and writes the PR sections |
| `security-review` | The review questions of the PR template, the PII, authorisation, safeguard and configurability rules, the change's impact on setup and feature gates, and the abuse cases |
| `code-quality-review` | Structure and maintainability against the architecture rules: missed simplification, one home per concern, logic in its layer, ad-hoc branching, files past 1,000 lines, types and boundaries |
| `role-manuals` | Manuals per role (beheerder, planner, medewerker, the people who book) and the adoption approach around them: grounded in what is built, walkthroughs on real screenshots from `verify-openplanner`, one Markdown source built to Word, PDF and HTML, white-label; an organisation's own material lives in a workspace outside the repository |

## Manuals, training and adoption

| Step | Skill | Why |
|---|---|---|
| Method, writing, build | `role-manuals` (repo) | The five steps, the form per role, the style guide, the capture story and the build to Word, PDF and HTML; run from the checkout on a workspace outside it |
| Screenshots and walkthroughs | `verify-openplanner` (repo) | The stack built from the checkout and its client (`scripts/stack-client.mjs`), shared with `role-manuals`: real user paths, fictional data, an evidence log |
| Word output | `anthropic-skills:docx` | docx-js conventions and output checks |
| Reader test | `anthropic-skills:doc-coauthoring` | Its reader-testing stage: a reader without context tries the role's tasks with the manual |
| Process pictures | `diagram` | A role's flow as Mermaid, SVG and PNG |
| After a release | `/document-release` | Lists documentation debt per change; `role-manuals` then updates text and screenshots from a checkout of the release tag, collects the release's changes per role into the document that says what is new, and freezes one version of the manuals per release |
| The participant pages | `ux-heuristics` | Finds friction that the FAQ or the page texts must cover |

The per-screen manual in `docs/manual/{nl,en}/` (ADR 0033) stays the built-in help; a role
manual links its screens into tasks and follows it.

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

## Evaluated, not adopted (2026-11-05)

- **ponytail** (an always-on "smallest change" mode): its rules contradict configurable by
  default (ADR 0042), the ports rule and the docstring rule, and its hooks inject them into
  every session and every subagent.
- **pstack** (Cursor's engineering-method pack): a second way of working next to gstack. Its
  shipping playbooks merge pull requests, and its comment reviewer removes the module and
  internal docstrings our docstring rule requires. Only its
  `create-verification-skill` generator is worth running, once, to write a repo verify skill.
- **thermo-nuclear** (Cursor's thermos plugin): its code-quality rubric and its parallel-review
  pattern live on, adapted, in `code-quality-review` and `pre-pr-review`. Its deep audit
  overlaps `security-review` and `/code-review`; its checks on developer setup, gate leaks and
  removed safeguards moved, rewritten for this repository, into `security-review`.

## Evaluated for manuals and adoption (2026-11-08)

Searched with `find-skills`: the open skills directory (skills.sh), the account's skills and the
plugin directory.

- **Worth installing when the work comes up** (Anthropic plugins, not installed): Customer
  Support `kb-article` (help articles and FAQ for participants), Operations `process-doc` (work
  instructions with an exceptions table), Product Management `stakeholder-update` (rollout
  updates per audience), Human Resources `onboarding` (a new staff member's first week, adapted
  per cohort). They work from pasted facts; their connectors are optional.
- **Not adopted:** community plugins with a `user-guide` skill (Snagly, Beegait): tied to their
  own QA or ticket flow; `design-skills` (`ux-writing`, `journey-mapping`): no upstream to
  review; Booking FAQ: built for deposits and walk-in businesses.
- **Not found:** skills for plain language, instructional design or change management. The
  directory's search needs a signed-in browser; its leaderboard had none. `role-manuals` covers
  that method.
