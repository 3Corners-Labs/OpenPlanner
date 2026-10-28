"""GitHub releases adapter (ADR 0029) against recorded responses."""

from collections.abc import Callable

import httpx
import pytest

from app.adapters.releases import GitHubReleases, running_version, update_service
from app.config import Settings
from app.domain.ports import IntegrationError
from app.domain.updates import Release, Version
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import DEMO_SUPERUSER

LATEST = {  # recorded shape of GET /repos/{owner}/{repo}/releases/latest (trimmed)
    "url": "https://api.github.com/repos/acme/openplanner/releases/1",
    "html_url": "https://github.com/acme/openplanner/releases/tag/v1.4.0",
    "tag_name": "v1.4.0",
    "name": "v1.4.0",
    "draft": False,
    "prerelease": False,
    "published_at": "2026-10-26T12:00:00Z",
    "body": "Release notes",
}


def _releases(
    handler: Callable[[httpx.Request], httpx.Response], token: str | None = None
) -> GitHubReleases:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubReleases(http, "acme/openplanner", token)


async def test_latest_release_is_read_from_the_tag_and_page() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=LATEST)

    release = await _releases(handler).latest()
    assert release == Release(Version(1, 4, 0), LATEST["html_url"])
    assert str(seen[0].url) == "https://api.github.com/repos/acme/openplanner/releases/latest"
    assert seen[0].headers["accept"] == "application/vnd.github+json"
    assert "authorization" not in seen[0].headers


async def test_token_is_sent_for_a_private_repository() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=LATEST)

    await _releases(handler, token="github_pat_x").latest()
    assert seen[0].headers["authorization"] == "Bearer github_pat_x"


async def test_no_release_yet_is_none() -> None:
    assert await _releases(lambda _: httpx.Response(404, json={})).latest() is None


@pytest.mark.parametrize(
    "body",
    [
        {**LATEST, "tag_name": "nightly"},
        {**LATEST, "html_url": "https://evil.example/notes"},
        {**LATEST, "html_url": "https://github.com/other/repo/releases/tag/v1.4.0"},
        {**LATEST, "tag_name": 5},
        {"message": "unexpected"},
        [],
    ],
)
async def test_a_release_that_is_no_version_or_has_a_foreign_page_is_none(
    body: object,
) -> None:
    assert await _releases(lambda _: httpx.Response(200, json=body)).latest() is None


@pytest.mark.parametrize(
    ("status", "code"), [(401, "unauthorized"), (429, "rate_limited"), (502, "server_error")]
)
async def test_refusals_become_integration_errors(status: int, code: str) -> None:
    with pytest.raises(IntegrationError) as caught:
        await _releases(lambda _: httpx.Response(status)).latest()
    assert caught.value.code == code


async def test_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(IntegrationError) as caught:
        await _releases(handler).latest()
    assert caught.value.code == "unreachable"


def test_running_version_comes_from_the_image() -> None:
    assert running_version(Settings(environment="dev", app_version="1.4.0")) == Version(1, 4, 0)
    assert running_version(Settings(environment="dev", app_version="main")) is None
    assert running_version(Settings(environment="dev")) is None


async def test_update_service_checks_only_with_a_repository() -> None:
    uow, clock = InMemoryUnitOfWork(), FixedClock()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as http:
        off = update_service(Settings(environment="dev"), uow, clock, http)
        on = update_service(
            Settings(environment="dev", update_repository="acme/openplanner"), uow, clock, http
        )
        assert not await off.check_if_due()
        assert await on.check_if_due()


async def test_web_process_never_looks_up_but_knows_the_worker_does() -> None:
    uow, clock = InMemoryUnitOfWork(), FixedClock()
    repository = Settings(environment="dev", update_repository="acme/openplanner")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as http:
        web = update_service(repository, uow, clock, http, lookup=False)
        assert not await web.check_if_due()
    assert (await web.overview(DEMO_SUPERUSER)).enabled
