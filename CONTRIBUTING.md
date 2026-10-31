# Contributing

## Setup

- [uv](https://docs.astral.sh/uv/) 0.13+, which installs Python 3.12 for you.
- Node.js, the LTS version in `NODE_VERSION` of `.github/workflows/ci.yml` (jscpd, e2e tooling,
  Renovate config check).
- Docker (for `docker compose up` and the container checks).

```sh
make setup         # uv sync --locked, e2e npm ci, .env from .env.example, pre-push hook
```

`make setup` points git at `.githooks/`; its `pre-push` hook runs the pin check of ADR 0051, so
a version Renovate cannot update is caught before CI. Every tool `make check` needs is pinned
and tracked by Renovate (shellcheck comes from PyPI through `uvx`, no Docker needed).

## Running

```sh
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload   # web
uv run python -m app.jobs.worker                         # worker
```

## Checks

CI has two lanes (ADR 0058). The **fast lane** runs on every push to a pull request and answers
in a few minutes. The **full lane** adds the authorisation matrix sweep, the container checks
and Playwright; it runs in the merge queue, on `main`, and on a pull request with the label
`ci:full`. Nothing reaches `main` without both. Add `ci:full` when a change touches
authorisation, navigation, templates or the widget, so those failures show up before the queue.

`make fast` is the fast lane on your machine, in about two minutes: ruff (format + lint),
shellcheck, mypy `--strict`, import-linter, pytest with coverage (≥ 90% on `app/domain/` and
`app/i18n/`, ≥ 75% overall) and bandit. Use it while trying an idea.

`make check` runs what every PR must pass locally: `make fast`, the authorisation matrix sweep
(`make matrix`, `pytest -m matrix`), pip-audit, `uv lock --check`, `make pins` and jscpd. Run
it before asking for review.

The tests run on every core (`pytest -n auto`, pytest-xdist). A single test or file still runs
on its own: `uv run pytest tests/domain/test_booking.py`. A test must not depend on another
test or on a shared path: use `tmp_path` and its own database (`tests/support/database.py`).

`make check` also runs the copy-paste detector (`make duplication`, jscpd; needs Node) and
shellcheck on `scripts/` and `.githooks/` (`make shellcheck`; the pinned `shellcheck-py` through `uvx`).
The host updater script is tested by `tests/scripts/test_updater.py` against fake `docker`,
`cosign`, `curl` and `flock` executables (`tests/fakes/updater_host.py`).

`make check` also runs `make pins` (ADR 0051): every action is pinned as `<sha> # vX.Y.Z`,
and any other version in a workflow, the Makefile, a shell script (the host installer and updater
among them) or a git hook has a `# renovate: datasource=… depName=…` line above it, or, for a
minimum version that must not follow the latest release, `# not a pin: <reason>`, so Renovate keeps it current. CI additionally fails on any deprecation warning.

Further targets: `make migrations` (Alembic up/down/up on a fresh database), `make licenses`
(runtime dependency licenses against the allow-list), `make semgrep` (repo rules self-test
plus scan), `make mutation` (mutmut on `app/domain`, score ≥ 80%), `make e2e` (Playwright,
axe-core and Lighthouse against a running app; pages listed in `e2e/pages.json`; the closure
pages need `docker compose exec web python -m app.cli seed-closures > e2e/fixtures.json` first,
which writes the link of the example booking on hold). Where the
Playwright browser download is blocked, use an installed Chrome:
`PLAYWRIGHT_CHROMIUM_CHANNEL=chrome npx playwright test --project=chromium` and
`CHROME_PATH=/path/to/chrome npm run lighthouse`. CI additionally runs gitleaks, hadolint, trivy, schemathesis, SBOM + grype, and on
`main` an OWASP ZAP baseline scan. See `.github/workflows/`.

## Layout

```
app/
  domain/      pure Python: entities, services, ports (Protocols). No framework imports.
  adapters/    implementations of the ports; adapters/db/ is the only place for SQL.
  api/         FastAPI routers and middleware.
  jobs/        worker loop and job handlers.
migrations/    Alembic.
tests/         mirrors app/; fakes for ports live in tests/fakes/.
```

## Writing code

- Every module, class and public function gets a Google-style docstring (enforced by ruff
  `D`). The module docstring says what the module is responsible for. Reference docs can be
  generated from it with pdoc, mkdocstrings or Sphinx.
- One home per concern: before adding a helper, look for an existing one; extract shared code
  at its second use (ADR 0005).
- Start with a failing test. Domain first, adapter second, HTTP last.
- New migration: `uv run alembic revision --autogenerate -m "..."`, then review it. Every
  migration must have a working `downgrade()`. Never edit a merged migration.
- New user-facing text gets a dotted key (`area.thing.detail`) in both
  `app/i18n/locales/nl.po` and `en.po`, and is used as `_("key")` in templates or
  `locale.gettext("key")` in code. Placeholders are `{name}`. `tests/i18n/test_catalogues.py`
  catches missing, unused and inconsistent keys.
- A new language: add `app/i18n/locales/<code>.po` (start from `nl.po`). Untranslated keys fall
  back to Dutch; the switcher picks it up automatically. Add the locale to `e2e/pages.json`.
- Every HTML page is rendered through `app.web.templates.render`, which applies the locale,
  the language switcher, CSP and caching headers. Do not build HTML responses elsewhere.
- Never put PII (`name, email, phone, note, guest_join_url, host_join_url`) in logs, errors,
  URLs or job payloads. The semgrep rules in `.semgrep/` and the masking test catch the obvious
  cases, not all of them.
- A new or changed admin screen comes with its manual topic in `docs/manual/nl` and
  `docs/manual/en` (ADR 0033, how-to in `docs/manual/README.md`). Citizen pages explain
  themselves in the page, through `_()`, and never link to the manual.
- A new dependency needs a reason in the PR, an active maintainer and a license in
  {MIT, BSD, Apache-2.0, PSF}. Anything else needs an ADR.

## Pull requests

Contributions are licensed under EUPL-1.2 and made under the
[Contributor Licence Agreement](docs/cla.md) (ADR 0054): you keep your copyright, 3Corners may
relicense, and every contribution stays available as open source. Tick its box in the pull
request description; CI fails a pull request without it unless its author can write to this
repository (organisation members, also with a private membership, and collaborators). After
ticking the box, re-run the job *licence and contributor agreement*; it reads the description
again. Code
copied from elsewhere gets its own entry in `REUSE.toml` with its copyright and licence;
CI runs `reuse lint`.

Short-lived branch, Conventional Commit title, squash merge after one human approval and green
CI. The PR template asks the seven review questions from `CLAUDE.md`; answer all of them.
New ADRs start from `docs/adr/template.md`, including its *Settings* section (ADR 0042). A new ADR also gets its *Build status* table and its row in `docs/adr-status.md`; the PR that builds a step of an ADR updates both (ADR 0059).
Run the `security-review` skill (`.claude/skills/security-review/`) before opening the PR.

`main` merges through a merge queue (squash): the queue builds `main` with the queued pull
requests on top and merges only when that build is green, so two pull requests that pass on
their own (two Alembic heads, two ADRs with one number) cannot both land. The queue also runs
the full lane, so a pull request that was green on the fast lane can still be sent back from
there.

## Releases

A merge to `main` is no release. Releases follow ADR 0052: `make release-plan` proposes the
number, a `chore(release): vX.Y.Z` PR sets it, and a tag on `main` publishes it. The commands
are in `docs/runbook.md`, *Releasing a version*.
`docs/skills.md` lists which Claude Code skills to use in each work package.
