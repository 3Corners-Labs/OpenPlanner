"""The updates page and banner (ADR 0029): superuser only, snooze per session, audited requests."""

from datetime import timedelta

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.update_service import UpdateService
from app.domain.updates import (
    Release,
    ReleaseCheck,
    RunningVersion,
    UpdateStatus,
    UpdateStep,
    Version,
)
from app.web.static_assets import ASSETS
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.domain import NOW

RELEASE = Release(Version(1, 4, 0), "https://github.com/acme/openplanner/releases/tag/v1.4.0")
BANNER = 'class="update-banner'
NOT_CHECKING = "zoekt niet naar nieuwe versies"


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    """The demo site running release 1.3.0; a test may pass more settings as its parameter."""
    extra = getattr(request, "param", {})
    return settings.model_copy(update={"app_version": "1.3.0", **extra})


@pytest.fixture
async def released(admin_db: str) -> None:
    """Store a release check that found 1.4.0, as the worker would."""
    engine = create_engine(admin_db)
    try:
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.save_release_check(ReleaseCheck(NOW, RELEASE))
    finally:
        await engine.dispose()


def updates(site: Site) -> UpdateService:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    found: UpdateService = app.state.update_service
    return found


@pytest.mark.usefixtures("released")
async def test_banner_tells_the_superuser_about_a_newer_release(site: Site) -> None:
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/")).text
    assert BANNER in page
    assert "1.4.0" in page
    assert "1.3.0" in page
    assert 'href="/admin/updates"' in page


@pytest.mark.usefixtures("released")
async def test_no_banner_and_no_page_for_an_organisation_admin(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    assert BANNER not in (await site.client.get("/admin/")).text
    assert (await site.client.get("/admin/updates")).status_code == 403


@pytest.mark.usefixtures("released")
async def test_snooze_hides_the_banner_for_an_hour(site: Site) -> None:
    await site.login_as(SUPER)
    snoozed = await site.client.post(
        "/admin/updates/snooze", data={"next": "/admin/audit"}, headers=SAME_ORIGIN
    )
    assert snoozed.status_code == 303
    assert snoozed.headers["location"] == "/admin/audit"
    assert BANNER not in (await site.client.get("/admin/")).text
    assert "1.4.0" in (await site.client.get("/admin/updates")).text  # the page stays
    site.clock.advance(timedelta(minutes=60))
    assert BANNER in (await site.client.get("/admin/")).text


@pytest.mark.parametrize("settings", [{"update_repository": "acme/openplanner"}], indirect=True)
async def test_page_knows_the_worker_checks_for_releases(site: Site) -> None:
    # Web never looks up releases itself (the worker does), but UPDATE_REPOSITORY is set.
    await site.login_as(SUPER)
    assert NOT_CHECKING not in (await site.client.get("/admin/updates")).text


async def test_page_says_when_nobody_checks_for_releases(site: Site) -> None:
    await site.login_as(SUPER)
    assert NOT_CHECKING in (await site.client.get("/admin/updates")).text


COMMIT = "ce5602a8f4e1b2c3d4e5f60718293a4b5c6d7e8f"


@pytest.mark.parametrize(
    "settings",
    [{"app_commit": COMMIT, "update_repository": "acme/openplanner"}],
    indirect=True,
)
async def test_page_names_the_commit_the_image_was_built_from(site: Site) -> None:
    # ADR 0052: version and commit together say which fixes run here.
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/updates")).text
    assert "<code>ce5602a8f4e1</code>" in page
    assert f'href="https://github.com/acme/openplanner/commit/{COMMIT}"' in page


async def test_page_says_when_the_commit_is_unknown(site: Site) -> None:
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/updates")).text
    assert "Commit" in page
    assert "/commit/" not in page


async def test_snooze_never_redirects_off_the_portal(site: Site) -> None:
    await site.login_as(SUPER)
    snoozed = await site.client.post(
        "/admin/updates/snooze", data={"next": "https://evil.example/"}, headers=SAME_ORIGIN
    )
    assert snoozed.headers["location"] == "/admin/"


@pytest.mark.usefixtures("released")
async def test_request_update_and_cancel_it(site: Site) -> None:
    await site.login_as(SUPER)
    requested = await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    assert requested.status_code == 303
    assert requested.headers["location"] == "/admin/updates?done=update_requested"
    page = (await site.client.get("/admin/updates")).text
    assert "1.4.0" in page
    history = await updates(site).overview(SUPER)
    assert history.open is not None
    cancel = f"/admin/updates/requests/{history.open.id}/cancel"
    cancelled = await site.client.post(cancel, headers=SAME_ORIGIN)
    assert cancelled.headers["location"] == "/admin/updates?done=request_cancelled"
    assert (await updates(site).overview(SUPER)).history[0].status is UpdateStatus.CANCELLED


@pytest.mark.usefixtures("released")
async def test_a_second_request_is_refused_on_the_page(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    again = await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    assert again.status_code == 409
    assert 'role="alert"' in again.text


async def test_request_without_a_release_is_refused(site: Site) -> None:
    await site.login_as(SUPER)
    refused = await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    assert refused.status_code == 409


@pytest.mark.usefixtures("released")
async def test_requests_need_the_same_origin(site: Site) -> None:
    await site.login_as(SUPER)
    forged = await site.client.post(
        "/admin/updates/update", headers={"sec-fetch-site": "cross-site"}
    )
    assert forged.status_code == 403
    assert (await updates(site).overview(SUPER)).open is None


@pytest.mark.usefixtures("released")
async def test_requests_need_a_fresh_login(site: Site) -> None:
    await site.login_as(SUPER)
    site.clock.advance(timedelta(minutes=61))
    stale = await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    assert stale.status_code == 303
    assert stale.headers["location"].startswith("/admin/login")
    assert (await updates(site).overview(SUPER)).open is None


@pytest.mark.usefixtures("released")
async def test_waiting_request_is_shown_in_the_banner(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    page = (await site.client.get("/admin/")).text
    assert BANNER in page
    assert "update-banner--progress" in page


PROGRESS = "/admin/updates/progress"


async def claimed_request(site: Site) -> int:
    """Request an update as the superuser and let the updater claim it; return its id."""
    await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    started = await updates(site).claim()
    assert started is not None
    return started.id or 0


def poll_url(html: str) -> str | None:
    """Return the fragment's poll URL, or ``None`` when it no longer polls."""
    marker = 'data-poll="'
    if marker not in html:
        return None
    return html.split(marker, 1)[1].split('"', 1)[0].replace("&amp;", "&")


@pytest.mark.usefixtures("released")
async def test_page_shows_the_steps_and_loads_the_poller_while_a_request_is_open(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    before = await site.client.get("/admin/updates")
    assert "update-progress.js" not in before.text
    assert "script-src" not in before.headers["content-security-policy"]
    await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    page = await site.client.get("/admin/updates")
    assert f'<script src="{ASSETS.url("update-progress.js")}" defer></script>' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "data-update-progress" in page.text
    assert 'aria-current="step"' in page.text
    assert "Wachten tot de updater het verzoek oppakt" in page.text
    assert BANNER not in page.text  # the page itself shows the request


@pytest.mark.usefixtures("released")
async def test_progress_fragment_answers_only_when_something_changed(site: Site) -> None:
    await site.login_as(SUPER)
    request_id = await claimed_request(site)
    first = await site.client.get(PROGRESS)
    assert first.status_code == 200
    assert "<html" not in first.text
    url = poll_url(first.text)
    assert url is not None
    assert url.startswith(PROGRESS + "?seen=")
    assert (await site.client.get(url)).status_code == 204
    await updates(site).report_step(request_id, UpdateStep.BACKING_UP)
    moved = await site.client.get(url)
    assert moved.status_code == 200
    assert "Back-up van de database maken" in moved.text
    assert poll_url(moved.text) not in (None, url)


@pytest.mark.usefixtures("released")
async def test_progress_fragment_stops_polling_with_the_outcome(site: Site) -> None:
    await site.login_as(SUPER)
    request_id = await claimed_request(site)
    await updates(site).report(request_id, UpdateStatus.ROLLED_BACK, "health_check_failed")
    ended = await site.client.get(PROGRESS + "?seen=x")
    assert ended.status_code == 200
    assert poll_url(ended.text) is None
    assert "Teruggedraaid" in ended.text
    assert 'href="/admin/updates"' in ended.text


@pytest.mark.usefixtures("released")
async def test_banner_elsewhere_names_the_current_step(site: Site) -> None:
    await site.login_as(SUPER)
    request_id = await claimed_request(site)
    await updates(site).report_step(request_id, UpdateStep.VERIFYING)
    page = (await site.client.get("/admin/")).text
    assert "update-banner--progress" in page
    assert "Handtekening controleren" in page


async def test_progress_fragment_refuses_an_organisation_admin(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    assert (await site.client.get(PROGRESS)).status_code == 403


@pytest.fixture
async def changed_outside(admin_db: str) -> None:
    """Two running versions, the newest without a request: a change made on the server."""
    engine = create_engine(admin_db)
    try:
        async with SqlUnitOfWork(engine).locked() as tx:
            await tx.add_running_version(RunningVersion("1.2.0", "a" * 40, NOW))
            await tx.add_running_version(
                RunningVersion("1.3.0", "b" * 40, NOW + timedelta(hours=1))
            )
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("released", "changed_outside")
async def test_the_history_names_who_did_what_and_warns_about_a_change_from_outside(
    site: Site,
) -> None:
    """ADR 0067: the history is the audit log of the updates, with a By column."""
    await site.login_as(SUPER)
    await site.client.post("/admin/updates/update", headers=SAME_ORIGIN)
    history = await updates(site).overview(SUPER)
    assert history.open is not None
    await site.client.post(f"/admin/updates/requests/{history.open.id}/cancel", headers=SAME_ORIGIN)

    page = (await site.client.get("/admin/updates")).text

    assert "buiten het portaal om" in page  # the notice about the change from outside
    assert "1.3.0" in page
    assert ">Door<" in page
    assert "Update aangevraagd" in page
    assert "Aanvraag ingetrokken" in page
    assert "<code>1.3.0</code> → <code>1.4.0</code>" in page
    assert 'href="/admin/audit?subject=updates"' in page
    # The updater's steps are not in the summary.
    assert "update.pulling" not in page
