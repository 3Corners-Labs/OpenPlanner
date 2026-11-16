"""A migrated SQLite database per test, its engine, and the world of the flow contract tests.

The rows the tests seed into it come from ``tests/support/seed_rows.py``.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.engine import create_engine
from tests.support.database import migrated_database
from tests.support.flow_backends import BACKENDS
from tests.support.flow_world import World, open_world


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "adapter")


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, busy_timeout_ms=200)
    yield engine
    await engine.dispose()


@pytest.fixture(params=BACKENDS)
async def world(request: pytest.FixtureRequest, database_url: str) -> AsyncIterator[World]:
    """The seeded world of the flow contract tests, on the SQL adapter and on the fake."""
    async with open_world(request.param, database_url) as seeded:
        yield seeded
