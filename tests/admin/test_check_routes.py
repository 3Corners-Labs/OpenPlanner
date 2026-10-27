"""The "Koppeling controleren" button on a group's settings page."""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.seed import seed_demo
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.checks import CheckArea, CheckStatus, Finding
from app.main import create_app
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, Site
from tests.admin.test_admin_flow import audit_events
from tests.fakes.clock import FixedClock
from tests.fakes.identity import FakeIdentityProvider
from tests.fakes.integrations import FakeChecker
from tests.support.domain import NOW

SETTINGS = "/admin/groups/demo/settings"
FINDINGS = [
    Finding(CheckArea.WEBEX, "webex.host_ok", CheckStatus.OK, (("host", "host@example.nl"),)),
    Finding(
        CheckArea.WXCC,
        "wxcc.schema.refused",
        CheckStatus.FAIL,
        (("missing", "meetingLink"), ("extra", "videoafspraak_Naam")),
    ),
    Finding(CheckArea.WXCC, "wxcc.screenpop.missing", CheckStatus.WARN),
    Finding(
        CheckArea.WEBHOOKS,
        "webhooks.delivery_failed",
        CheckStatus.FAIL,
        (("endpoint", "wxcc"), ("outcome", "proxy_error")),
    ),
    Finding(CheckArea.WEBHOOKS, "webhooks.future_code", CheckStatus.WARN),
]


@pytest.fixture
async def checked(settings: Settings, admin_db: str) -> AsyncIterator[tuple[Site, FakeChecker]]:
    engine = create_engine(admin_db)
    await seed_demo(engine)
    await engine.dispose()
    idp, checker = FakeIdentityProvider(), FakeChecker(FINDINGS)
    app = create_app(
        settings.model_copy(update={"database_url": admin_db}),
        FixedClock(NOW),
        idp,
        Integrations(checker=checker),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield Site(client, idp, FixedClock(NOW)), checker


async def test_the_settings_page_offers_the_check(checked: tuple[Site, FakeChecker]) -> None:
    site, checker = checked
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(SETTINGS)).text

    assert 'action="/admin/groups/demo/check"' in page
    assert "Deze groep is nog niet gecontroleerd." in page
    assert checker.calls == []  # opening the page checks nothing


async def test_checking_shows_translated_findings_per_area(
    checked: tuple[Site, FakeChecker],
) -> None:
    site, checker = checked
    await site.login_as(ORG_ADMIN)

    response = await site.client.post("/admin/groups/demo/check", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert response.headers["location"] == f"{SETTINGS}?done=checked#check"
    page = (await site.client.get(response.headers["location"])).text
    assert "De koppeling is gecontroleerd." in page
    assert 'Uitslag: <span class="chip chip--danger">Fout</span>' in page
    assert "Webex (videogesprek)" in page
    assert "Host host@example.nl is bekend" in page
    assert "velden die ontbreken: meetingLink" in page
    assert "Geen Screen Pop" in page
    # Delivery outcomes are translated with the integrations page's labels.
    assert "Testbericht naar wxcc: " in page
    assert "proxy_error" not in page
    assert "Onbekende uitkomst (webhooks.future_code)." in page
    assert checker.calls == [("demo", ["demo"])]  # peers: the demo organisation's groups only
    assert ("sub-org", "integration.check", "failure", "fail") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]


async def test_english_texts(checked: tuple[Site, FakeChecker]) -> None:
    site, _ = checked
    await site.login_as(ORG_ADMIN)
    await site.client.post("/admin/groups/demo/check", headers=SAME_ORIGIN)

    page = (await site.client.get(SETTINGS, params={"lang": "en"})).text

    assert "Check integration" in page
    assert "Webex Contact Center refuses the work item" in page


async def test_cross_site_check_is_refused(checked: tuple[Site, FakeChecker]) -> None:
    site, checker = checked
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        "/admin/groups/demo/check", headers={"sec-fetch-site": "cross-site"}
    )

    assert response.status_code == 403
    assert checker.calls == []
