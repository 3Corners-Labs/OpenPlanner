"""The admin cookie's name and attributes follow ``PUBLIC_BASE_URL`` (ADR 0098 point 12)."""

import re
from pathlib import Path

import httpx
import pytest

from app.admin.session import session_cookie_for
from app.config import Settings
from app.main import create_app
from tests.admin.conftest import SUPER, Site
from tests.fakes.clock import FixedClock
from tests.fakes.identity import FakeIdentityProvider
from tests.support.domain import NOW

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def test_https_gives_the_host_prefixed_secure_cookie() -> None:
    cookie = session_cookie_for("https://plan.example.org")

    assert (cookie.name, cookie.path, cookie.secure) == ("__Host-op_admin", "/", True)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8010"])
def test_http_keeps_the_plain_cookie(url: str) -> None:
    cookie = session_cookie_for(url)

    assert (cookie.name, cookie.path, cookie.secure) == ("op_admin", "/admin", False)


def _login_cookie_headers(response: httpx.Response) -> list[str]:
    return response.headers.get_list("set-cookie")


async def _login_set_cookie(settings: Settings, **updates: str) -> str:
    """Start a login on a fresh app and return the ``Set-Cookie`` line of the session."""
    app = create_app(settings.model_copy(update=updates), FixedClock(NOW), FakeIdentityProvider())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client,
    ):
        response = await client.get("/admin/login", follow_redirects=False)
    lines = _login_cookie_headers(response)
    assert len(lines) == 1
    return lines[0]


def _attributes(line: str) -> list[str]:
    """Return the attributes after the cookie value, lower-cased (Starlette writes them so)."""
    return [part.strip().lower() for part in line.split(";")[1:]]


async def test_https_url_sets_the_prefixed_cookie_with_secure(settings: Settings) -> None:
    line = await _login_set_cookie(settings, public_base_url="https://plan.example.org")

    assert line.startswith("__Host-op_admin=")
    attributes = _attributes(line)
    assert {"path=/", "secure", "httponly", "samesite=lax"} <= set(attributes)
    assert not any(a.startswith("domain") for a in attributes)


async def test_secure_follows_the_url_not_the_environment(settings: Settings) -> None:
    line = await _login_set_cookie(
        settings, public_base_url="https://plan.example.org", environment="test"
    )

    assert line.startswith("__Host-op_admin=")
    assert "secure" in _attributes(line)


async def test_http_url_in_development_keeps_op_admin_without_secure(settings: Settings) -> None:
    line = await _login_set_cookie(
        settings, public_base_url="http://127.0.0.1:8000", environment="dev"
    )

    assert line.startswith("op_admin=")
    assert "path=/admin" in _attributes(line)
    assert "secure" not in _attributes(line)


async def test_a_public_page_never_touches_the_admin_cookie(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.get("/g/demo")

    assert response.status_code in {200, 404}
    assert _login_cookie_headers(response) == []


_SESSION_USE = re.compile(r"request\.session|\.session\[|\.session\.(get|pop|clear)|\"session\"")


def test_no_code_outside_the_admin_package_reads_or_writes_the_session() -> None:
    offenders = [
        str(path.relative_to(APP_ROOT))
        for path in sorted(APP_ROOT.rglob("*.py"))
        if path.relative_to(APP_ROOT).parts[0] != "admin"
        and _SESSION_USE.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_no_template_reads_the_session() -> None:
    offenders = [
        str(path.relative_to(APP_ROOT))
        for path in sorted(APP_ROOT.rglob("*.html"))
        if "request.session" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
