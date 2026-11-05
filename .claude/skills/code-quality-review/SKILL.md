---
name: code-quality-review
description: Strict maintainability review of the current branch against main. Looks for the restructuring that makes a change simpler, a second home for a concern, logic in the wrong layer, ad-hoc branching, files growing past 1,000 lines and loose types, judged against the architecture rules in CLAUDE.md. Use for a "code quality review", "maintainability review", "thermo-nuclear review" or "is this the simplest structure"; `pre-pr-review` runs it before every PR.
---

# Code quality review (repo skill)

Review the diff of the current branch against `main` (`git diff main...HEAD`, plus uncommitted
changes) for structure and maintainability. Correctness and security have their own reviewers
(`/code-review`, `security-review`); the question here is whether the change leaves the codebase
simpler or messier. Read every changed file in full, and the code around each change: its
callers, the module it lands in, and the existing helpers for the same job. Code the diff does
not touch is in scope only where the change leaves it inconsistent, such as another copy of the
rule the diff changes or a caller the diff should have moved too; report it under the finding
that causes it. Report findings; do not fix them in this step.

Aim high. "This could be tidier" is not the bar. For every meaningful change, look for the
reframing that makes branches, flags, helpers or whole layers disappear, so that the result looks
obvious in hindsight: the code-judo move. Deleting complexity beats moving it around.

## Required here, never a finding

A generic reviewer reads these as indirection. In this repository they are rules (CLAUDE.md), so
judge only whether the change uses them the way the rest of the code does.

- A Protocol in `app/domain/ports.py` for every external system, with one adapter and a fake in
  `tests/fakes/`; persistence through the `Transaction` Protocol.
- A setting with a class, default and bounds for every tunable value, declared in
  `app/domain/settings_registry.py` (ADR 0042). A new hard-coded threshold is the finding, never
  the setting. Fixed rules stay constants (WCAG targets, PII fields, crypto parameters, protocol
  limits such as the 254-character address of RFC 5321); for those, a second copy of the same
  constant is the finding (check 2).
- A Google-style docstring on every module, class and public function.
- One entry in the navigation register `app/web/navigation.py` per admin screen (ADR 0047), one
  permission per admin route through `RequirePermission(Permission.…)`, and every HTML response
  through `app.web.templates.render`.
- User-facing text through `_()` with `nl` and `en` entries; every logged or audited action
  declared in `app/domain/log_events.py` (ADR 0103).
- The layer order that import-linter enforces: `app.main` → `app.api | app.admin | app.jobs` →
  `app.web` → `app.adapters` → `app.domain`.

## Checks

1. **Missed simplification.** Is there a restructuring, inside the rules above, that removes
   whole branches, flags, helpers or layers? Name it and say what it deletes. A refactor that
   moves code without lowering the number of concepts a reader must hold is not a
   simplification.
2. **One home per concern** (ADR 0005). Flag a second mechanism for something the code already
   does: another polling script, rate limiter, settings reader, permission check, time-zone
   conversion or HTTP client setup, or a near-copy of an existing helper. Name the existing one
   and its path. Shared behaviour is extracted at its second use, not its third. jscpd catches
   copied text; this check catches the same idea written twice.
3. **Logic in its layer.** Business rules live in `app/domain/` as pure functions and types, IO
   in `app/adapters/`, HTML plumbing in `app.web`; routes, jobs and the CLI only wire them
   together. Flag a rule decided in a route, template, job or adapter, and feature logic inside a
   shared module. Import-linter checks imports, not where a rule is decided.
4. **Ad-hoc branching.** Flag a special case inserted into an existing flow: an `if` on one
   group, organisation, channel or provider inside general code, a boolean parameter that
   switches a function between two jobs, a nullable "mode" argument. Prefer a typed model, a
   table or register, a strategy behind a Protocol, or a per-group setting, so that the flow
   keeps one path. The same condition repeated in several places points to a missing type.
5. **Size.** A change that pushes a file in `app/` or `scripts/` past 1,000 lines needs a strong
   reason in the PR: ask whether the new code should be its own module first. The same holds
   when a file already past 1,000 lines grows by more than about 50 lines of logic. A declarative
   register (settings, logged actions, models, navigation entries) that grows by entries is not a
   finding; logic added to one is. Declarations (a dataclass, a Protocol method, an enum member)
   count as entries; function bodies and branches count as logic. Split a function that does several unrelated jobs by job,
   never by line count, and never into helpers that only exist to make it shorter.
6. **Types and boundaries.** Flag `Any`, `cast()`, `# type: ignore`, an `Optional` that no path
   sets to `None`, and `dict[str, Any]` handed across a layer where a dataclass, Pydantic model or
   domain type belongs. Parse outside input once, at the edge (`app/api`, `app/admin`, the
   boundary of an adapter), and trust the domain types inside. Flag a silent fallback that papers
   over an unclear invariant; ask for the invariant to be made explicit instead. mypy --strict
   proves the types agree, not that they are the right ones.
7. **Thin or clever abstractions.** Flag a wrapper that only forwards calls, a class with one
   method and no state, a base class with a single subclass that the rules above do not ask for,
   and generic machinery (dispatch on strings, `getattr` lookups, dynamic imports, metaclasses)
   where a plain function or a register would do.
8. **Atomic, ordered work.** Related writes belong in one transaction, and cancelling or
   rescheduling a booking deletes its pending jobs in that same transaction (CLAUDE.md). Flag an
   update that can leave state half applied, and orchestration that is more sequential or keeps
   more state than the job needs.
9. **Tests that survive a refactor.** Tests mirror the source tree and assert what a caller can
   observe. Flag a test that pins internals (a private helper, the call order of fakes), so that
   a change preserving behaviour would break it; a test whose name promises a behaviour its
   assertions never observe; and set-up copied between test files instead of shared through a
   fixture.

## Severity

- **high**, a blocker unless the PR gives a convincing reason: a clear structural regression; a
  visible simplification that would delete much of the change's incidental complexity is left
  unused; a second home for an existing concern; a business rule in the wrong layer; a file pushed
  past 1,000 lines; special-case branching that tangles a shared flow; state that can be left
  half applied in a way that loses or corrupts data.
- **medium:** an extraction missed at the second use, in code or in test set-up; loose types
  across a boundary; a thin wrapper; a file past 1,000 lines grown further; a test that pins
  internals or never observes what its name promises; half-applied state that a retry repairs.
- **low:** local readability, a name that hides intent. Report low findings only when there is
  nothing higher, and at most five.

A few findings you are sure of beat a long list. No style points that ruff already enforces, no
"consider", and no finding without a concrete better structure. Before you report one, open the
code it depends on: a finding that says "unless X already handles this" is unfinished.

## Tone

Plain and specific. When a change makes the code messier, or misses a large simplification, say
so; do not soften a structural problem into a suggestion. Write "this adds a third special case
to the same function; one typed policy removes all three", not "maybe consider refactoring".

## Output

A Markdown section ready to paste into the PR description:

```
### Code quality review
**Verdict:** ready / restructure first: <finding numbers>
**Findings:** numbered; severity (high/medium/low); file:line; the problem in one or two
sentences; the structure you propose; what it deletes
**Size:** files pushed past or grown beyond 1,000 lines, or "none"
**Not checked:** what you could not read or judge, if it mattered
```
