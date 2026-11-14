# 0127. Graph-guided development, reuse and unused code are part of the build

Status: accepted, partly built (2026-11-14), requested by the product owner: the Graphify code
graph must be a fixed part of how we build and keep improving, quality and reuse must stay high,
and unused routines must be removed as part of the build. Extends ADR 0005 (one home per
concern) and ADR 0058 (the fast lane). Step 1 is built in the pull request that adds it; steps 2
and 3 are planned; see *Build status*.

## Context

The Graphify MCP indexes this repository (nodes for functions, classes, files and tests; edges
for calls, imports and test links) and reviews every pull request for coupling. Until now using
it was left to habit, and nothing in the build looked for code that nothing calls. `ruff` finds
an unused import or local variable inside one file, `jscpd` finds copies, `import-linter` holds
the layers; no check finds a function, class or constant that no other code uses. The graph
cannot enforce anything alone: it is an external service that describes the commit it was built
from, so a CI gate cannot depend on it, and it never replaces reading the code.

## Decision

1. **Graph first, file second.** For structure (definition, callers, blast radius, linked
   tests, imports) a session asks the graph before it greps or reads files; for text, settings,
   templates, i18n keys and anything changed in the branch it reads the file. CLAUDE.md, *How to
   work in this repo*, holds the table of questions and tools. A graph answer is never a finding
   and never proof of the current code.
2. **Reuse before writing.** Before a new helper, class or constant, the author looks for an
   existing home with `graphify_find` and `query_graph`. `pre-pr-review` lists the new public
   symbols of the diff and, for each, the nearest existing symbol that does a similar job; the
   `code-quality-review` skill judges it. `jscpd` stays the gate for copies.
3. **Unused code fails the build.** `make deadcode` runs vulture on `app/` at a confidence of 80
   or more, in `make fast` and in the `python` job of CI. At that level vulture reports what is
   certain: an unused variable, argument, import or unreachable code. A finding is fixed by
   deleting the code. A name that a signature must carry (a framework's hook) goes into
   `ignore_names` of `[tool.vulture]` in `pyproject.toml` with the reason in the comment next to
   it; a whole module or directory is never ignored.
4. **A sweep for unused routines.** Vulture matches names across files, so an unused function or
   class sits at 60% confidence, together with routes, jobs, settings and hooks that a framework
   calls by name. These are candidates, not findings. At each minor release (and any time a
   session works in an area) the candidates are checked with the graph: a candidate with no
   caller in `graphify_callers`, no linked test other than its own and no registration by name
   (route, template, job kind, setting key, i18n key, entry point; found with a text search) is
   deleted together with its tests, in its own `refactor:` pull request. A candidate that stays
   is named in the pull request with the reason.
5. **Hotspots are watched.** At each minor release the release pull request lists the ten
   symbols with the highest coupling in the Graphify review (callers plus callees), and names
   those that grew since the last release, as the input for a refactor decision. Graphify's pull
   request review stays advisory.
6. **The process improves.** A session that finds the graph wrong, stale or missing a fact
   records it in the pull request, and the skills and CLAUDE.md are changed in the pull request
   that found it, not left as a lesson.

## Settings

None. The confidence floor (80) and the vulture version are build-tool constants, not behaviour
of the running product; the version is pinned in the `Makefile` with a Renovate annotation
(ADR 0051).

**Fixed on purpose:** the floor of 80 for the gate. A lower floor would fail the build on
framework entry points; a higher one would miss unused variables.

## Consequences

- vulture (MIT) runs through `uvx` and is not a project dependency; recorded in
  `docs/dependencies.md`.
- The gate catches little today (the four findings at 80 are listed in `ignore_names`); its
  value is that new dead code cannot enter `app/` unnoticed. The 60% candidates (about 500 now,
  mostly routes and hooks) are the work of step 2.
- Vulture matches by name, so a name used anywhere hides unused code with the same name
  elsewhere. The graph sweep covers that.
- Graph quality depends on a fresh index; each answer says how old it is.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #TBD | merged |
| 1 | `make deadcode` and `[tool.vulture]`, in `make fast` and the CI `python` job, pinned and tracked by Renovate; the points of the process in CLAUDE.md, `docs/skills.md`, `pre-pr-review` and `code-quality-review` (reuse list, unused code) | #TBD | merged |
| 2 | The first graph sweep of the 60% candidates, deleted in `refactor:` pull requests | | planned |
| 3 | The hotspot list in the release pull request (`scripts/release_pr.py`) | | planned |
