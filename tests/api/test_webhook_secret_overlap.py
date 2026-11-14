"""The installation's own webhook secrets rotate with an overlap (ADR 0124 point 8).

The secrets come from the environment, which the application cannot write: an optional previous
value verifies for the overlap counted from the moment the application first saw the current
secret, kept in the database as a fingerprint. A restart does not start the overlap again, a
changed secret does, a store that cannot be read grants nothing, and no secret or fingerprint
reaches a log.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
import structlog.testing
from fastapi import FastAPI, Request
from pydantic import SecretStr
from sqlalchemy import text

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlTransaction, SqlUnitOfWork
from app.adapters.webhooks import WEBEX_INBOUND, WXCC_INBOUND, sign_webex, sign_wxcc
from app.config import Settings
from app.domain.webhook_secrets import SecretFinding
from app.main import create_app
from app.web.webhook_secrets import install_installation_secrets, installation_status
from tests.fakes.clock import FixedClock
from tests.support.database import migrated_database
from tests.support.domain import NOW
from tests.support.webhooks import wxcc_body

OLD = "old-installation-secret-0123456789"
NEW = "new-installation-secret-9876543210"
WEBEX_BODY = json.dumps(
    {
        "resource": "meetings",
        "event": "started",
        "data": {"id": "m-1", "meetingSeriesId": "series-1"},
    },
    separators=(",", ":"),
).encode()


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "webhook-secret-overlap")


def _settings(settings: Settings, database_url: str, **update: object) -> Settings:
    return settings.model_copy(
        update={
            "database_url": database_url,
            "webex_webhook_secret": SecretStr(NEW),
            "wxcc_webhook_secret": SecretStr(NEW),
            "webex_webhook_secret_previous": SecretStr(OLD),
            "wxcc_webhook_secret_previous": SecretStr(OLD),
            **update,
        }
    )


@asynccontextmanager
async def _running(settings: Settings, clock: FixedClock) -> AsyncIterator[httpx.AsyncClient]:
    """A fresh application process on the same database: a restart when used twice."""
    app = create_app(settings, clock)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client


async def _wxcc(client: httpx.AsyncClient, clock: FixedClock, secret: str) -> int:
    body = wxcc_body("task:connected", at=clock.now())
    stamp = int(clock.now().timestamp() * 1000)
    response = await client.post(
        "/webhooks/wxcc", content=body, headers=sign_wxcc(body, secret, stamp)
    )
    return response.status_code


async def _webex(client: httpx.AsyncClient, secret: str) -> int:
    response = await client.post(
        "/webhooks/webex", content=WEBEX_BODY, headers=sign_webex(WEBEX_BODY, secret)
    )
    return response.status_code


async def test_the_previous_value_verifies_for_the_overlap_and_then_stops(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    async with _running(_settings(settings, database_url), clock) as client:
        inside = (
            await _wxcc(client, clock, OLD),
            await _wxcc(client, clock, NEW),
            await _webex(client, OLD),
            await _webex(client, NEW),
        )
        clock.advance(timedelta(minutes=59))
        still = (await _wxcc(client, clock, OLD), await _webex(client, OLD))
        clock.advance(timedelta(minutes=1))
        after = (
            await _wxcc(client, clock, OLD),
            await _wxcc(client, clock, NEW),
            await _webex(client, OLD),
            await _webex(client, NEW),
        )

    assert inside == (204, 204, 204, 204)
    assert still == (204, 204)
    assert after == (401, 204, 401, 204)


async def test_without_a_previous_value_the_old_secret_is_refused_at_once(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    none = _settings(
        settings,
        database_url,
        wxcc_webhook_secret_previous=None,
        webex_webhook_secret_previous=None,
    )
    async with _running(none, clock) as client:
        assert (await _wxcc(client, clock, OLD), await _webex(client, OLD)) == (401, 401)


async def test_an_overlap_of_nothing_refuses_the_previous_value_at_once(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    async with _running(
        _settings(settings, database_url, webhook_secret_overlap_minutes=0), clock
    ) as client:
        assert (await _wxcc(client, clock, OLD), await _webex(client, OLD)) == (401, 401)
        assert (await _wxcc(client, clock, NEW), await _webex(client, NEW)) == (204, 204)


async def test_a_restart_does_not_start_the_overlap_again_but_a_changed_secret_does(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    async with _running(_settings(settings, database_url), clock) as client:
        assert await _wxcc(client, clock, OLD) == 204
    clock.advance(timedelta(minutes=61))

    async with _running(_settings(settings, database_url), clock) as restarted:
        after_restart = await _wxcc(restarted, clock, OLD)
    third = "third-installation-secret-5555555555"
    async with _running(
        _settings(
            settings,
            database_url,
            wxcc_webhook_secret=SecretStr(third),
            wxcc_webhook_secret_previous=SecretStr(NEW),
        ),
        clock,
    ) as rotated:
        on_rotation = (await _wxcc(rotated, clock, NEW), await _wxcc(rotated, clock, third))

    assert after_restart == 401  # the same secret, first seen an hour ago: its overlap is over
    assert on_rotation == (204, 204)  # a new secret: its own overlap begins when first seen


async def test_a_rotation_of_the_management_key_keeps_the_first_sighting(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    key = "m" * 40
    first = _settings(settings, database_url, management_token_key=SecretStr(key))
    async with _running(first, clock) as client:
        assert await _wxcc(client, clock, OLD) == 204
    clock.advance(timedelta(minutes=61))
    new_key = "n" * 40
    rotated = _settings(
        settings,
        database_url,
        management_token_key=SecretStr(new_key),
        management_token_key_previous=SecretStr(key),
    )

    async with _running(rotated, clock) as client:
        refused = await _wxcc(client, clock, OLD)

    assert refused == 401  # the fingerprint under the previous key matched: not first seen now


async def test_only_a_keyed_fingerprint_and_a_time_are_stored_never_a_secret(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    async with _running(_settings(settings, database_url), clock) as client:
        await _wxcc(client, clock, NEW)

    engine = create_engine(database_url)
    async with engine.connect() as connection:
        columns = (await connection.execute(text("SELECT * FROM webhook_secret_sightings"))).keys()
        rows = (await connection.execute(text("SELECT * FROM webhook_secret_sightings"))).all()
    await engine.dispose()
    assert list(columns) == ["platform", "fingerprint", "first_seen"]
    ((platform, made, first_seen),) = rows
    assert platform == "wxcc"
    assert NEW not in repr(rows)
    assert OLD not in repr(rows)
    assert len(made) == 64
    assert str(first_seen).startswith(NOW.strftime("%Y-%m-%d %H:%M"))


async def test_a_store_that_cannot_be_read_grants_no_overlap(
    settings: Settings, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: SqlTransaction, platform: str) -> None:
        raise OSError("the database is locked")

    monkeypatch.setattr(SqlTransaction, "get_secret_sighting", broken)
    clock = FixedClock(NOW)

    async with _running(_settings(settings, database_url), clock) as client:
        with structlog.testing.capture_logs() as logs:
            results = (await _wxcc(client, clock, OLD), await _wxcc(client, clock, NEW))

    assert results == (401, 204)  # an overlap that cannot be measured is not granted
    assert OLD not in repr(logs)
    assert NEW not in repr(logs)


async def test_no_secret_or_fingerprint_reaches_a_log(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    with structlog.testing.capture_logs() as logs:
        async with _running(_settings(settings, database_url), clock) as client:
            await _wxcc(client, clock, OLD)
            clock.advance(timedelta(minutes=61))
            await _wxcc(client, clock, OLD)
            await _webex(client, OLD)

    text = repr(logs)
    assert OLD not in text
    assert NEW not in text
    assert "fingerprint" not in text


# --- what System status says -------------------------------------------------------------------


@asynccontextmanager
async def _request(settings: Settings, clock: FixedClock, url: str) -> AsyncIterator[Request]:
    """A request whose application has the settings and the service, as ``app.main`` builds them."""
    engine = create_engine(url)
    state = SimpleNamespace(settings=settings, clock=clock, engine=engine)
    app = SimpleNamespace(state=state)
    install_installation_secrets(
        cast(FastAPI, app), SqlUnitOfWork(engine), clock, lambda: cast(Settings, state.settings)
    )
    try:
        yield cast(Request, SimpleNamespace(app=app))
    finally:
        await engine.dispose()


async def test_a_previous_value_still_set_after_its_overlap_is_a_finding(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    async with _request(_settings(settings, database_url), clock, database_url) as request:
        inside = await installation_status(request, WXCC_INBOUND)
        video_inside = await installation_status(request, WEBEX_INBOUND)
        clock.advance(timedelta(minutes=60))
        after = await installation_status(request, WXCC_INBOUND)
        video = await installation_status(request, WEBEX_INBOUND)

    assert inside is not None
    assert inside.finding is None
    assert inside.secrets.previous == OLD
    assert video_inside is not None
    assert video_inside.finding is None
    assert after is not None
    assert after.finding is SecretFinding.ENDED
    assert video is not None
    assert video.finding is SecretFinding.ENDED


async def test_a_previous_value_equal_to_the_current_one_is_a_finding_and_is_not_used(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    same = _settings(settings, database_url, wxcc_webhook_secret_previous=SecretStr(NEW))
    async with _request(same, clock, database_url) as request:
        status = await installation_status(request, WXCC_INBOUND)

    assert status is not None
    assert status.finding is SecretFinding.SAME
    assert status.secrets.previous is None


async def test_no_finding_without_a_previous_value_and_none_without_a_secret(
    settings: Settings, database_url: str
) -> None:
    clock = FixedClock(NOW)
    bare = _settings(
        settings, database_url, wxcc_webhook_secret_previous=None, webex_webhook_secret=None
    )
    async with _request(bare, clock, database_url) as request:
        without = await installation_status(request, WXCC_INBOUND)
        missing = await installation_status(request, WEBEX_INBOUND)

    assert without is not None
    assert without.finding is None
    assert missing is None
