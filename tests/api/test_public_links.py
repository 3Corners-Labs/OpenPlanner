"""Links follow PUBLIC_BASE_URL and never the Host header or the request's scheme (ADR 0086).

The tunnel trial of 1 November 2026: with a proxy that FORWARDED_ALLOW_IPS did not name, the
app's own redirect from ``/admin`` to ``/admin/`` went to ``http://``. Every link is built on
the public address; one middleware keeps the one link Starlette builds on its own there too.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.web.public_links import PublicRedirectMiddleware
from tests.support.app_client import app_client

APP = Path(__file__).resolve().parents[2] / "app"
PUBLIC = "https://afspraak.example.nl"
EVIL = "evil.example"


@pytest.fixture
def public_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"public_base_url": PUBLIC})


async def test_a_slash_redirect_goes_to_the_public_address_not_to_the_host_header(
    public_settings: Settings,
) -> None:
    async with app_client(public_settings) as client:
        add = await client.get("/admin", headers={"host": EVIL}, follow_redirects=False)
        drop = await client.get("/admin/login/", headers={"host": EVIL}, follow_redirects=False)
        query = await client.get(
            "/admin/login/?next=/admin", headers={"host": EVIL}, follow_redirects=False
        )

    assert add.status_code == 307
    assert add.headers["location"] == f"{PUBLIC}/admin/"
    assert drop.headers["location"] == f"{PUBLIC}/admin/login"
    assert query.headers["location"] == f"{PUBLIC}/admin/login?next=/admin"


async def rewritten(settings: Settings, location: str, host: str = EVIL) -> str:
    """Send a redirect with ``location`` through the middleware for a request to ``host``."""

    async def app(scope: Any, receive: Any, send: Any) -> None:
        headers = [(b"location", location.encode())]
        await send({"type": "http.response.start", "status": 307, "headers": headers})

    sent: list[str] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(dict(message["headers"])[b"location"].decode())

    scope = {
        "type": "http",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": "/",
        "query_string": b"",
        "headers": [(b"host", host.encode())],
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }
    await PublicRedirectMiddleware(app)(scope, None, send)  # type: ignore[arg-type]
    return sent[0]


@pytest.mark.parametrize(
    "location",
    [
        "/admin/",  # relative: nothing to rewrite
        "https://idp.test/authorize?x=1",  # another site
        f"http://{EVIL}.nl/x",  # starts like the request's origin, is another host
        f"http://{EVIL}:8080/x",  # another port is another origin
    ],
)
async def test_a_redirect_that_is_not_to_the_requests_own_origin_is_left_alone(
    public_settings: Settings, location: str
) -> None:
    assert await rewritten(public_settings, location) == location


async def test_a_redirect_to_the_requests_own_origin_is_rewritten_even_for_a_forged_host(
    public_settings: Settings,
) -> None:
    assert await rewritten(public_settings, f"http://{EVIL}/g/demo") == f"{PUBLIC}/g/demo"
    assert await rewritten(public_settings, f"http://{EVIL}") == PUBLIC


_ADDRESS_FROM_REQUEST = re.compile(
    r"\b(?:request|self\._request)\.(?:base_url|url_for)\b"
    r"|\burl_for\("
    r"|\brequest\.url\b(?!\.(?:path|scheme|netloc|query)\b)"
)
_OUTGOING = {"adapters/http_trace.py"}
"""Reads the URL of a request the app *sent* (the trace of an integration call)."""


def test_no_module_builds_an_absolute_url_from_the_request() -> None:
    # What is checked here by CI was checked by reading (ADR 0086 point 4). ``request.url.path``
    # (a path to compare), ``.scheme`` (the Secure flag of a cookie) and ``.netloc`` (the
    # same-origin test of a POST) are not links.
    offenders = [
        f"{path.relative_to(APP)}:{number}: {line.strip()}"
        for path in APP.rglob("*.py")
        if path.relative_to(APP).as_posix() not in _OUTGOING
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _ADDRESS_FROM_REQUEST.search(line) and not line.lstrip().startswith("#")
    ]

    assert offenders == []


def test_the_scan_sees_each_way_of_reading_the_requests_address() -> None:
    for line in (
        "x = request.base_url",
        "x = request.url_for('a')",
        "x = url_for('a')",
        "x = str(request.url)",
        "x = request.url.host",
    ):
        assert _ADDRESS_FROM_REQUEST.search(line), line
    for line in ("x = request.url.path", "x = request.url.scheme == 'https'"):
        assert not _ADDRESS_FROM_REQUEST.search(line), line
