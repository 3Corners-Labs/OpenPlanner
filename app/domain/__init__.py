"""Domain: scheduling, booking and retention rules in pure Python.

No imports from FastAPI, SQLAlchemy, httpx, pydantic-settings, structlog or any adapter.
Domain errors carry machine-readable codes, never user-facing text: translation happens at
the edges via ``app.i18n`` (see ``docs/adr/0004``).
"""
