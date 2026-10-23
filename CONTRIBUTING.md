# Contributing

## Setup

- [uv](https://docs.astral.sh/uv/) 0.13+, which installs Python 3.12 for you.
- Docker (for `docker compose up` and the container checks).

```sh
uv sync            # creates .venv with runtime + dev dependencies from uv.lock
cp .env.example .env
```

## Running

```sh
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload   # web
uv run python -m app.jobs.worker                         # worker
```

## Checks

`make check` runs what every PR must pass locally: ruff (format + lint), mypy `--strict`,
import-linter, pytest with coverage (≥ 90% on `app/domain/`, ≥ 75% overall), bandit,
pip-audit and `uv lock --check`.

`make check` also runs the copy-paste detector (`make duplication`, jscpd; needs Node).

Further targets: `make migrations` (Alembic up/down/up on a fresh database), `make licenses`
(runtime dependency licenses against the allow-list), `make semgrep` (repo rules self-test
plus scan), `make mutation` (mutmut on `app/domain`, score ≥ 80%), `make e2e` (Playwright,
axe-core and Lighthouse against a running app; pages listed in `e2e/pages.json`). Where the
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
- New user-facing text gets a dotted key in `app/i18n` with `nl` and `en` entries (ADR 0004).
- Never put PII (`name, email, phone, note, guest_join_url, host_join_url`) in logs, errors,
  URLs or job payloads. The semgrep rules in `.semgrep/` and the masking test catch the obvious
  cases, not all of them.
- A new dependency needs a reason in the PR, an active maintainer and a license in
  {MIT, BSD, Apache-2.0, PSF}. Anything else needs an ADR.

## Pull requests

Short-lived branch, Conventional Commit title, squash merge after one human approval and green
CI. The PR template asks the five review questions from `CLAUDE.md`; answer all of them.
Run the `security-review` skill (`.claude/skills/security-review/`) before opening the PR.
`docs/skills.md` lists which Claude Code skills to use in each work package.
