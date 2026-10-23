"""Fixtures of the participant-page tests: a migrated database with the demo group."""

from pathlib import Path

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.config import Settings
from tests.support.database import migrated_database


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    # Synchronous: Alembic runs its own event loop.
    return migrated_database(tmp_path, monkeypatch, "pages")


@pytest.fixture
async def demo_settings(settings: Settings, database_url: str) -> Settings:
    url = database_url
    engine = create_engine(url)
    await seed_demo(engine)
    await engine.dispose()
    return settings.model_copy(update={"database_url": url})
