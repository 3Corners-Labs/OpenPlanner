"""Fixtures of the participant-page and API tests: the demo data, the widget and machine API.

``site`` is the app with the demo group embeddable on ``SITE`` (the widget's tests); a module
that needs another site defines its own ``site``. ``agent`` is the app with the machine API on.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.config import Settings
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.agent_api import Agent, agent_api
from tests.support.database import migrated_database
from tests.support.domain import NOW
from tests.support.widget import embed


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


@pytest.fixture
async def site(demo_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """The app with the demo data, the demo group embeddable on ``SITE``."""
    await embed(demo_settings)
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        yield client


@pytest.fixture
async def agent(settings: Settings, database_url: str) -> AsyncIterator[Agent]:
    """The app with the demo data and the machine API switched on in env and portal."""
    on = settings.model_copy(update={"database_url": database_url, "agent_api_enabled": True})
    async for running in agent_api(on, enabled=True):
        yield running
