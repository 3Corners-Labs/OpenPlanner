"""The updates page and banner (ADR 0029): superuser only, snooze per session, audited requests."""

from datetime import timedelta

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.config import Settings
from app.domain.update_service import UpdateService
from app.domain.updates import Release, ReleaseCheck, UpdateStatus, Version
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
