# 0058. A fast lane on the pull request, the full lane in the merge queue

Status: accepted (2026-10-30), requested by the product owner. Amends CLAUDE.md
("Code style and quality gates", *Per PR*) and point 2 of ADR 0012. Keeps ADR 0006 and
ADR 0051 as they are. Partly built; see `docs/adr-status.md` for what is built.

## Context

Every pull request runs the whole gate set of CLAUDE.md, and then the merge queue (#112) runs
it again on `main` plus the queue. Both runs take 12–22 minutes, so a change waits 35–45
minutes from push to `main`. Trying an idea out has become slow enough that people avoid it.

Measured on 2026-10-30 (CI runs 40981683883 and 40978137374, and locally on 4 workers):

| Part | CI | Local, 4 workers |
|---|---|---|
| Every job except `python`, `e2e`, `container` | < 1 min each, in parallel | |
| `container` (image, trivy, schemathesis, SBOM, grype) | 4 min | |
| `e2e` (Playwright, axe, Lighthouse) | 7 min | |
| `python`: ruff, mypy, import-linter, migrations, bandit | 20 s together | |
| `python`: pytest with coverage | **13–20 min** | 6 min 40 s |
| of which `tests/admin/test_authorization_matrix.py` (3,664 of 7,810 tests) | | 2 min 12 s without coverage |
| of which every other test (4,146) | | 1 min 20 s without coverage, 2 min 5 s with |

So one step sets the pace: pytest. Within it, the authorisation matrix takes about 60% of
the time, because each of its cases migrates a database, seeds the demo data, starts the app
and logs in (`tests/admin/conftest.py`, `site`). Coverage tracing roughly doubles the time
of the tests it runs over. Python 3.12 cannot use the faster `sys.monitoring` core for branch
coverage.

Without the matrix, the remaining tests still give 97% total coverage, and `app/domain/` and
`app/i18n/` stay above 90%. The coverage gates do not need the matrix.

Alternatives considered:

- **A release branch with fewer tests on the way in.** Rejected. It delays the tests instead
  of skipping them, so failures from several changes arrive together at release time and have
  to be untangled. It also breaks trunk-based development and squash merges (CLAUDE.md, *Git
  and process*), and it adds a second merge for every change.
- **Fewer gates.** Rejected. Every gate guards a rule in CLAUDE.md, and most cost less than a
  minute.
- **A faster CI runner.** Possible later. It costs money and leaves the structure as it is.

## Decision

1. **Two lanes, one workflow.** `ci.yml` keeps every existing job and its name, and gains
   one (point 3). Each job is in one lane:
   - **Fast lane** (on `pull_request`, `merge_group` and `push` to `main`): ruff, shellcheck,
     mypy, import-linter, pytest *without* the matrix sweeps but *with* the coverage
     gates (domain and i18n ≥ 90%, total ≥ 75%), Alembic up and down, bandit, jscpd, lockfile/pins/audit/licences, semgrep, gitleaks,
     `reuse lint` and the contributor agreement. Target: under 5 minutes on a pull request.
   - **Full lane** (on `merge_group` and `push` to `main`, and on a pull request with the
     label `ci:full`): the matrix sweeps, `container` and `e2e`. The workflow also listens to
     `labeled`, so adding the label starts the full lane without a new push.
   On a pull request without the label, the full-lane jobs end as *skipped*. GitHub counts a
   required check that a job condition skipped as passed, so the pull request can enter the
   queue. The merge queue runs both lanes, so nothing reaches `main` without the full set.

2. **The matrix sweeps get a marker.** The parametrised cases of
   `tests/admin/test_authorization_matrix.py` (`test_matrix`,
   `test_matrix_during_maintenance`, `test_anonymous_visitors_are_sent_to_the_login`) carry
   `@pytest.mark.matrix`, which is registered in `pyproject.toml`. The structural tests in that
   file stay in the fast lane: every admin route declares one permission, and every role has
   expectations. A new route without a permission is therefore still caught on the pull
   request; only the sweep over role × route waits for the queue.

3. **The matrix gets its own job.** The `python` job stays in the fast lane and runs
   `pytest -m "not matrix"` with coverage. A new full-lane job `authorisation matrix` runs
   `pytest -m matrix` without coverage, in parallel with `container` and `e2e`. The full lane
   then takes as long as its slowest job, not the sum of them, and the queue run gets shorter
   as well.

4. **Make targets match the lanes.** `make fast` runs the fast lane locally, in about 2 minutes
   on a laptop. `make matrix` runs the sweeps. `make check` is `make fast` plus `make matrix`
   and the dependency, pin and duplication checks. CONTRIBUTING.md says to use `make fast` while
   trying an idea, and `make check` before asking for review.

5. **ADR 0012 point 2 reads, from now on:** every check of the pull request is green on its
   current head (the fast lane, or both lanes with `ci:full`), and the merge happens only
   through the merge queue, which runs the full lane. Claude Code adds the PR to the queue; it
   never merges outside it.

6. **Step 2 (a separate PR): make the matrix itself cheaper, without fewer cases.** For
   example, seed the demo data once into the template database (as `tests/support/database.py`
   already does for migrations) and start one app per worker for the read-only GET cases.
   The target is under 3 minutes in CI. The cases and their expected statuses stay exactly
   the same.

7. **Unchanged:** the runs on merge to `main` (ZAP baseline, mutmut, signing; `main.yml`),
   the weekly and daily jobs, and every threshold in CLAUDE.md. Only *when* a gate runs
   changes, not *whether*.

## Settings

None. This decision changes CI configuration (`.github/workflows/ci.yml`, `Makefile`,
`pyproject.toml` markers), not the application. No setting in `app/config.py` or
`app/domain/settings_registry.py` changes.

**Fixed on purpose:** which job runs in which lane, the `matrix` marker and the label name
`ci:full`. They live in the workflow and are reviewed like code; they are not tunable at run
time.

## Consequences

- Measured on this ADR's own pull request (#122, run 41036008137, both lanes): `python`
  (fast lane, pytest with coverage, 4,149 tests) 6 minutes, `authorisation matrix` 6 minutes,
  `container` 4.5 minutes, `e2e` 8.5 minutes, all in parallel. A pull request without
  `ci:full` therefore answers in about 6 minutes instead of 12–22. A queue run takes about
  8.5 minutes, set by `e2e`. From push to `main` that is roughly 15 minutes instead of 35–45.
- A pull request can be green and still fail in the merge queue on the matrix, e2e or the
  container. The queue then removes it, and `main` stays green. The author learns
  this later than today. Pull requests that touch authorisation, navigation, templates or the
  widget should use `ci:full` to find out early. The PR template says so.
- To update in the same PR: CLAUDE.md (*Per PR* becomes *Fast lane* and *Before merge*),
  CONTRIBUTING.md (`make fast`, the label, the merge queue), `.github/pull_request_template.md`
  (when to use `ci:full`). `docs/architecture.md` does not show the pipeline.
- Branch protection gets one more required check (`authorisation matrix`); the existing ones
  stay. The product owner changes that setting. The label `ci:full` has to be
  created once in the repository.
- No dependency is added.
