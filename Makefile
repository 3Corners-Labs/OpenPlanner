.PHONY: check fmt lint type imports test security audit lock licenses migrations semgrep duplication mutation e2e up down

UV := uv run

check: lint type imports test security audit lock duplication ## Everything a PR must pass locally

fmt:
	$(UV) ruff format .
	$(UV) ruff check --fix .

lint:
	$(UV) ruff format --check .
	$(UV) ruff check .

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

# Runtime dependencies only (dev tools never ship). Exceptions need an ADR: see docs/adr/0003.
licenses:
	scripts/check-licenses.sh

migrations:
	DATABASE_URL=sqlite+aiosqlite:///$$(mktemp -d)/migrate.db sh -c '$(UV) alembic upgrade head && $(UV) alembic downgrade base && $(UV) alembic upgrade head'

semgrep:
	uvx semgrep --test --config .semgrep/rules.yml .semgrep/rules.py
	uvx semgrep scan --error --config p/python --config p/owasp-top-ten --config p/secrets --metrics=off --config .semgrep/rules.yml app/

duplication:
	npx --yes jscpd@4.0.5

# Mutates app/domain and app/i18n; runs once either has tests, then enforces >= 80%.
mutation:
	@if ls tests/domain/test_*.py tests/i18n/test_*.py 2>/dev/null | grep -q .; then \
		$(UV) mutmut run && $(UV) mutmut export-cicd-stats; \
	fi
	$(UV) python scripts/check_mutation_score.py --min 80

# Needs a running app (docker compose up) at BASE_URL, default http://127.0.0.1:8000.
e2e:
	cd e2e && npm ci && npx playwright install && npm test && npm run lighthouse

up:
	docker compose up --build

down:
	docker compose down
