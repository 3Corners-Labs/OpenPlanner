"""Shared fixtures: isolated settings per test and an in-process HTTP client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.ports import Clock
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        worker_heartbeat_path=tmp_path / "worker.heartbeat",
        worker_poll_seconds=0.01,
        management_token_key=SecretStr("t" * 32),
        dev_token_key_path=tmp_path / "dev-token.key",
        session_secret=SecretStr("x" * 32),
        rate_limit_enabled=False,
    )


@pytest.fixture
def unreachable_db_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings whose database cannot be opened: a directory is not a SQLite file."""
    return settings.model_copy(update={"database_url": f"sqlite+aiosqlite:///{tmp_path}"})


@asynccontextmanager
async def app_client(
    settings: Settings, clock: Clock | None = None, integrations: Integrations | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan and yield a client that talks to it in-process."""
    app = create_app(settings, clock, integrations=integrations)
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
