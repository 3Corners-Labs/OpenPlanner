"""Migrations stand alone, so any chain of released versions runs in order (ADR 0116 point 5).

A migration that imports application code changes meaning whenever that code changes, and a
database several minors behind could no longer be upgraded step by step. The one allowed
import is the column type ``UTCDateTime``, a pure SQLAlchemy type decorator.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = sorted((ROOT / "migrations" / "versions").glob("[0-9]*.py"))
ALLOWED = {"app.adapters.db.types"}
"""The application modules a migration may import."""


def app_imports(source: str) -> set[str]:
    """Return the ``app`` modules that ``source`` imports, wherever in the file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found.update(n for n in names if n == "app" or n.startswith("app."))
    return found


def test_there_are_migrations() -> None:
    assert VERSIONS


def test_migrations_import_no_application_code_beyond_the_column_type() -> None:
    problems = {
        path.name: sorted(imports - ALLOWED)
        for path in VERSIONS
        if (imports := app_imports(path.read_text())) - ALLOWED
    }

    assert problems == {}


def test_the_check_finds_what_it_is_meant_to() -> None:
    assert app_imports("from app.adapters.db.models import Base") == {"app.adapters.db.models"}
    assert app_imports("def upgrade():\n    import app.config") == {"app.config"}
    assert app_imports("import sqlalchemy as sa\nfrom alembic import op") == set()
