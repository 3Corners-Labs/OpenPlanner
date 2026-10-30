from pathlib import Path

import httpx
from pydantic import SecretStr

from app.adapters.db.engine import create_engine
from app.adapters.integrations import build_integrations, webex_fingerprint
from app.config import Settings
from tests.fakes.clock import FixedClock


async def test_nothing_is_built_without_a_service_app(tmp_path: Path) -> None:
    settings = Settings(environment="dev")
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/i.db")
    async with httpx.AsyncClient() as http:
        built = build_integrations(settings, engine, http, FixedClock())
    await engine.dispose()

    assert (built.tokens, built.video, built.dispatcher) == (None, None, None)


async def test_wxcc_needs_its_api_host(tmp_path: Path) -> None:
    base = {
        "environment": "dev",
        "webex_client_id": "client",
        "webex_client_secret": SecretStr("secret"),
        "webex_refresh_token": SecretStr("refresh"),
        "dev_settings_key_path": tmp_path / "settings.key",
    }
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/i.db")
    async with httpx.AsyncClient() as http:
        video_only = build_integrations(Settings(**base), engine, http, FixedClock())  # type: ignore[arg-type]
        both = build_integrations(
            Settings(**base, wxcc_api_base_url="https://api.wxcc-eu1.cisco.com"),  # type: ignore[arg-type]
            engine,
            http,
            FixedClock(),
        )
    await engine.dispose()

    assert video_only.video is not None
    assert video_only.dispatcher is None
    assert both.dispatcher is not None


def test_the_webex_fingerprint_changes_with_the_service_app_only() -> None:
    base = Settings(environment="dev")
    same = base.model_copy(update={"log_level": "DEBUG"})
    other = base.model_copy(update={"webex_client_id": "client-2"})

    assert webex_fingerprint(base) == webex_fingerprint(same)
    assert webex_fingerprint(base) != webex_fingerprint(other)
    assert "client-2" not in webex_fingerprint(other)
