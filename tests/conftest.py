"""Shared fixtures: isolated settings per test and an in-process HTTP client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        worker_heartbeat_path=tmp_path / "worker.heartbeat",
        worker_poll_seconds=0.01,
    )


@pytest.fixture
def unreachable_db_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings whose database cannot be opened: a directory is not a SQLite file."""
    return settings.model_copy(update={"database_url": f"sqlite+aiosqlite:///{tmp_path}"})


@asynccontextmanager
async def app_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan and yield a client that talks to it in-process."""
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(settings) as client:
        yield client
