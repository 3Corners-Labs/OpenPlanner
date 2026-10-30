"""GitHub releases adapter (ADR 0029) against recorded responses."""

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.releases import GitHubReleases, plain_notes, running_version, update_service
from app.config import Settings
from app.domain.ports import IntegrationError
from app.domain.updates import NOTES_CHARS, ListedRelease, Version
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.domain import DEMO_SUPERUSER

LATEST = {  # recorded shape of one item of GET /repos/{owner}/{repo}/releases (trimmed)
    "url": "https://api.github.com/repos/acme/openplanner/releases/1",
    "html_url": "https://github.com/acme/openplanner/releases/tag/v1.4.0",
    "tag_name": "v1.4.0",
    "name": "v1.4.0",
    "draft": False,
    "prerelease": False,
    "published_at": "2026-10-26T12:00:00Z",
    "body": (
        "## What's Changed\n"
        "* feat(queue): own queue by @cyberbeuk in https://github.com/acme/openplanner/pull/88\n"
        "* fix(video): time zone by @someone in https://github.com/acme/openplanner/pull/90\n"
        "\n\n**Full Changelog**: https://github.com/acme/openplanner/compare/v1.3.0...v1.4.0"
    ),
}
RELEASE = ListedRelease(
    Version(1, 4, 0),
    LATEST["html_url"],
    datetime(2026, 10, 26, 12, tzinfo=UTC),
    "feat(queue): own queue (#88)\nfix(video): time zone (#90)",
)


def _releases(
    handler: Callable[[httpx.Request], httpx.Response], token: str | None = None
) -> GitHubReleases:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubReleases(http, "acme/openplanner", token)


async def test_the_listing_is_read_from_tag_page_time_and_notes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[LATEST])

    listing = await _releases(handler).listing()
    assert listing == [RELEASE]
    assert str(seen[0].url) == "https://api.github.com/repos/acme/openplanner/releases?per_page=30"
    assert seen[0].headers["accept"] == "application/vnd.github+json"
    assert "authorization" not in seen[0].headers


async def test_token_is_sent_for_a_private_repository() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[LATEST])

    await _releases(handler, token="github_pat_x").listing()
    assert seen[0].headers["authorization"] == "Bearer github_pat_x"


async def test_no_release_yet_is_an_empty_listing() -> None:
    assert await _releases(lambda _: httpx.Response(404, json={})).listing() == []
    assert await _releases(lambda _: httpx.Response(200, json=[])).listing() == []
    assert await _releases(lambda _: httpx.Response(200, json={"x": 1})).listing() == []


@pytest.mark.parametrize(
    "item",
    [
        {**LATEST, "tag_name": "nightly"},
        {**LATEST, "html_url": "https://evil.example/notes"},
        {**LATEST, "html_url": "https://github.com/other/repo/releases/tag/v1.4.0"},
        {**LATEST, "tag_name": 5},
        {**LATEST, "draft": True},
        {"message": "unexpected"},
        "text",
    ],
)
async def test_a_release_that_is_no_version_a_draft_or_a_foreign_page_is_skipped(
    item: object,
) -> None:
    listing = await _releases(lambda _: httpx.Response(200, json=[item, LATEST])).listing()
    assert listing == [RELEASE]


async def test_candidates_are_listed_with_releases_and_odd_dates_are_none() -> None:
    candidate = {
        **LATEST,
        "html_url": "https://github.com/acme/openplanner/releases/tag/v1.5.0-rc.2",
        "tag_name": "v1.5.0-rc.2",
        "prerelease": True,
        "published_at": "yesterday",
        "body": None,
    }
    listing = await _releases(lambda _: httpx.Response(200, json=[candidate, LATEST])).listing()
    assert listing[0] == ListedRelease(Version(1, 5, 0, 2), candidate["html_url"], None, "")
    assert listing[1] == RELEASE


def test_notes_become_plain_pr_titles_and_numbers() -> None:
    assert plain_notes(LATEST["body"]) == RELEASE.notes
    assert (
        plain_notes("- [docs](https://x.example) by @me in https://github.com/a/b/pull/7")
        == "docs (#7)"
    )
    assert plain_notes("") == ""
    assert len(plain_notes("x" * 5000)) == NOTES_CHARS


@pytest.mark.parametrize(
    ("status", "code"), [(401, "unauthorized"), (429, "rate_limited"), (502, "server_error")]
)
async def test_refusals_become_integration_errors(status: int, code: str) -> None:
    with pytest.raises(IntegrationError) as caught:
        await _releases(lambda _: httpx.Response(status)).listing()
    assert caught.value.code == code


async def test_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(IntegrationError) as caught:
        await _releases(handler).listing()
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


async def test_the_channel_decides_what_the_check_calls_the_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0059, 0067: the adapter lists everything; the channel is applied by the domain."""
    candidate = {
        **LATEST,
        "html_url": "https://github.com/acme/openplanner/releases/tag/v1.5.0-rc.2",
        "tag_name": "v1.5.0-rc.2",
        "prerelease": True,
    }
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[candidate, LATEST]))
    )
    monkeypatch.setenv("UPDATE_REPOSITORY", "acme/openplanner")
    monkeypatch.setenv("APP_VERSION", "1.3.0")
    for channel, latest in (("release", Version(1, 4, 0)), ("rc", Version(1, 5, 0, 2))):
        monkeypatch.setenv("UPDATE_CHANNEL", channel)
        uow = InMemoryUnitOfWork()
        service = update_service(Settings(environment="test"), uow, FixedClock(), http)
        assert await service.check_if_due()
        overview = await service.overview(DEMO_SUPERUSER)
        assert overview.check is not None
        assert overview.check.latest is not None
        assert overview.check.latest.version == Version(1, 5, 0, 2)  # the newest of all
        assert overview.newest is not None
        assert overview.newest.version == latest  # the newest on this channel
        assert [item.version for item in overview.offered][0] == latest
        assert len(uow.state.release_listing) == 2
