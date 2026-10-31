"""A login is bound to the provider it started at (ADR 0083 point 14, abuse case B5)."""

import pytest
from starlette.requests import Request

from app.admin import routes
from app.admin.session import pop_login_start, save_login_start
from app.domain.audit import Outcome
from app.domain.identity import PLATFORM
from app.domain.ports import LoginStart
from tests.admin.conftest import GROUP_ADMIN, Site
from tests.admin.test_admin_flow import audit_events

START = LoginStart("https://idp.test/authorize", "state-1", "nonce-1", "verifier-1")


def request() -> Request:
    return Request({"type": "http", "session": {}})


def test_the_pending_login_records_its_provider() -> None:
    req = request()
    save_login_start(req, START, "/admin/", provider="gem-a")

    pending = pop_login_start(req)

    assert pending is not None
    assert pending["provider"] == "gem-a"


def test_a_login_started_without_a_provider_belongs_to_the_platform() -> None:
    req = request()
    save_login_start(req, START, "/admin/")

    pending = pop_login_start(req)

    assert pending is not None
    assert pending["provider"] == PLATFORM


async def test_the_login_page_starts_at_the_platform_provider(site: Site) -> None:
    await site.client.get("/admin/login")
    site.idp.next_principal = GROUP_ADMIN

    response = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert response.status_code == 303


async def test_b5_a_callback_for_a_provider_that_is_not_known_is_refused(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = routes.save_login_start

    def elsewhere(req: Request, start: LoginStart, next_url: str, provider: str = PLATFORM) -> None:
        original(req, start, next_url, provider="gem-x")

    monkeypatch.setattr(routes, "save_login_start", elsewhere)
    await site.client.get("/admin/login")
    site.idp.next_principal = GROUP_ADMIN

    response = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert response.status_code == 400
    assert ("anonymous", "admin.login", Outcome.DENIED, "invalid_state") in await audit_events(site)
