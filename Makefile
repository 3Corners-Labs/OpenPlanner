.PHONY: setup check pins fmt lint shellcheck type imports test security audit lock licenses migrations semgrep duplication mutation release-plan e2e proxy-check diagrams up down

UV := uv run

# One command from a fresh clone to a machine that runs `make check` (ADR 0051): Python
# environment, e2e tooling, .env from the example, and the pre-push hook that runs `make pins`.
setup:
	@command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/" >&2; exit 1; }
	@command -v node >/dev/null || { echo "install Node.js $$(sed -n 's/^  NODE_VERSION: "\(.*\)"/\1/p' .github/workflows/ci.yml) (LTS) first" >&2; exit 1; }
	uv sync --locked
	cd e2e && npm ci
	@[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }
	git config core.hooksPath .githooks
	@echo "setup done; run 'make check' before a PR"

check: lint shellcheck type imports test security audit lock pins duplication ## Everything a PR must pass locally

fmt:
	$(UV) ruff format .
	$(UV) ruff check --fix .

lint:
	$(UV) ruff format --check .
	$(UV) ruff check .

# The host's shell scripts (the root-run updater among them) and the git hooks. The pinned
# shellcheck from PyPI, so CI and every laptop run the same version without Docker.
# renovate: datasource=pypi depName=shellcheck-py
SHELLCHECK_VERSION := 0.11.0.1
shellcheck:
	uvx --from shellcheck-py==$(SHELLCHECK_VERSION) shellcheck scripts/*.sh scripts/updater/*.sh .githooks/*

type:
	$(UV) mypy

imports:
	$(UV) lint-imports

test:
	$(UV) pytest --cov --cov-report=term --cov-fail-under=75
	$(UV) coverage report --include='app/domain/*,app/i18n/*' --fail-under=90

security:
	$(UV) bandit -q -c pyproject.toml -r app/

audit:
	uv export --frozen --no-emit-project --format requirements-txt > .audit-requirements.txt
	$(UV) pip-audit --strict --disable-pip -r .audit-requirements.txt; status=$$?; rm -f .audit-requirements.txt; exit $$status

lock:
	uv lock --check

# ADR 0051: every pinned version is one Renovate can update, and renovate.json is valid.
# renovate: datasource=npm depName=renovate
RENOVATE_VERSION := 44.193.0
pins:
	$(UV) python -m scripts.check_pins
	npx --yes --package renovate@$(RENOVATE_VERSION) renovate-config-validator --strict renovate.json

# Runtime dependencies only (dev tools never ship). Exceptions need an ADR: see docs/adr/0003.
licenses:
	scripts/check-licenses.sh

migrations:
	ENVIRONMENT=dev DATABASE_URL=sqlite+aiosqlite:///$$(mktemp -d)/migrate.db sh -c '$(UV) alembic upgrade head && $(UV) alembic downgrade base && $(UV) alembic upgrade head'

semgrep:
	uvx semgrep --test --config .semgrep/rules.yml .semgrep/rules.py
	uvx semgrep scan --error --config p/python --config p/owasp-top-ten --config p/secrets --metrics=off --config .semgrep/rules.yml app/

# renovate: datasource=npm depName=jscpd
JSCPD_VERSION := 5.4.2
duplication:
	npx --yes jscpd@$(JSCPD_VERSION)

# Architecture plates in docs/diagrams from scripts/diagrams/content.py. Needs the draw.io desktop
# app and Chrome locally; python-docx is added for this run only (not a project dependency).
# renovate: datasource=pypi depName=python-docx
PYTHON_DOCX_VERSION := 1.2.0
diagrams:
	uv run --with python-docx==$(PYTHON_DOCX_VERSION) python -m scripts.diagrams.build

# Mutates app/domain and app/i18n; runs once either has tests, then enforces >= 80%.
mutation:
	@if ls tests/domain/test_*.py tests/i18n/test_*.py 2>/dev/null | grep -q .; then \
		$(UV) mutmut run && $(UV) mutmut export-cicd-stats; \
	fi
	$(UV) python scripts/check_mutation_score.py --min 80

# What the next release would be, from the commits since the last tag (ADR 0051). Changes
# nothing; docs/runbook.md, "Releasing a version", says what to do with the answer.
release-plan:
	@git fetch --quiet --tags origin
	$(UV) python -m scripts.release_plan

# TLS proxy (ADR 0030) against Let's Encrypt's test CA; needs docker.
proxy-check:
	scripts/check-proxy.sh

# Needs a running app (docker compose up) at BASE_URL, default http://127.0.0.1:8000.
e2e:
	cd e2e && npm ci && npx playwright install && npm test && npm run lighthouse

up:
	docker compose up --build

down:
	docker compose down
