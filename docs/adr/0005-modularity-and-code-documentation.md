# 0005. Modularity and code documentation

Status: accepted (2026-10-23)

## Context

The code is largely written with AI assistance and reviewed by humans. Two risks follow:
code that nobody can explain without reading it line by line, and near-duplicate fragments
that drift apart. The project also wants reference documentation that standard tools can
generate.

## Decision

1. **Docstrings everywhere in `app/`, `migrations/` and `scripts/`**, Google style, enforced
   by ruff (`D`, `convention = "google"`). Every module docstring states the module's
   responsibility and, where relevant, what it must not do. pdoc, mkdocstrings and Sphinx
   (napoleon) can all generate reference documentation from this without extra markup. Tests
   are exempt: their names describe the behaviour.
2. **Layers enforced by import-linter.** `app.main` (composition root) → entry points
   (`app.api`, `app.admin`, `app.jobs`, never importing each other) → `app.adapters` →
   `app.domain` (framework-free). `app.i18n` gets its own independence contract (ADR 0004).
3. **One home per concern.** Shared behaviour is extracted at the second use, not the third.
   Examples: `is_reachable()` for database probes, `scripts/run-app-container.sh` for CI,
   `tests/conftest.py::app_client`. A copy-paste detector (jscpd: at least 6 lines or 50
   tokens, threshold 1%) runs on `app/` and `tests/` (`widget/` from work package 3) and fails
   CI.
4. **Ports and adapters.** Every external system sits behind a Protocol. Adapters are
   swappable and tested with recorded fixtures; the domain is tested with fakes.
5. **Secure by design.** Deny by default (security headers, CORS allow-list, `require_scope`
   on every admin route). Validate input at the boundary (Pydantic). Use typed value objects in
   the domain instead of loose strings. Errors are codes, not text. PII never reaches logs
   (masking plus semgrep). Secrets come only from the environment.

## Consequences

A PR that adds a module without a docstring, crosses a layer or copies a block fails CI. The
reviewer checks that a new module's responsibility does not overlap an existing one.
