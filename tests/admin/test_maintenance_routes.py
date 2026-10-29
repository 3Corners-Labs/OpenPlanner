"""The maintenance page and bar (ADR 0038): superuser only, fresh login, audited, read only.

Abuse cases from docs/threat-model.md: an organisation admin writing during maintenance (also
covered route by route in ``test_authorization_matrix``) and switching off what the
environment keeps on.
"""

from datetime import timedelta

import pytest

from app.config import Settings
from app.domain.maintenance import MaintenanceReason
from app.domain.maintenance_service import MaintenanceService
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site

BAR = 'id="maintenance-bar"'


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    """The demo site; a test may pass more settings as its parameter."""
    return settings.model_copy(update=getattr(request, "param", {}))


def maintenance(site: Site) -> MaintenanceService:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    found: MaintenanceService = app.state.maintenance_service
    return found


async def test_superuser_switches_on_with_an_expected_end_and_off(site: Site) -> None:
    await site.login_as(SUPER)
    assert BAR not in (await site.client.get("/admin/")).text

    response = await site.client.post(
        "/admin/maintenance/on",
        data={"reason": "integration", "expected_end": "01-01-2026", "expected_end_time": "12:30"},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/maintenance?done=maintenance_on"

    page = (await site.client.get("/admin/maintenance?done=maintenance_on")).text
    assert BAR in page
    assert "Koppeling herstellen" in page
    assert "Verwacht klaar: 1 jan 2026, 12:30" in page
    assert "<code>sub-super</code>" in page

    response = await site.client.post("/admin/maintenance/off", headers=SAME_ORIGIN)
    assert response.status_code == 303
    assert BAR not in (await site.client.get("/admin/")).text


async def test_the_bar_is_on_every_admin_page_for_every_role(site: Site) -> None:
    await maintenance(site).switch_on_host("host-cli", MaintenanceReason.OTHER)

    await site.login_as(ORG_ADMIN)
    for path in ("/admin/", "/admin/groups/demo", "/admin/audit"):
        page = (await site.client.get(path)).text
        assert BAR in page, path
        assert "Alleen lezen" in page


async def test_an_expected_end_in_the_past_is_refused_on_its_field(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        "/admin/maintenance/on",
        data={"reason": "other", "expected_end": "01-01-2020", "expected_end_time": "12:00"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert 'href="#expected_end"' in response.text
    assert not (await maintenance(site).stored()).active


async def test_an_unknown_reason_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        "/admin/maintenance/on", data={"reason": "because"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert not (await maintenance(site).stored()).active


async def test_switching_is_refused_cross_site(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        "/admin/maintenance/on", data={"reason": "other"}, headers={"sec-fetch-site": "cross-site"}
    )

    assert response.status_code == 403
    assert not (await maintenance(site).stored()).active


async def test_switching_needs_a_fresh_login(site: Site) -> None:
    await site.login_as(SUPER)
    site.clock.advance(timedelta(hours=2))  # past ADMIN_REAUTH_MINUTES, within the session

    response = await site.client.post(
        "/admin/maintenance/on", data={"reason": "other"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login")
    assert not (await maintenance(site).stored()).active


async def test_a_refused_change_shows_the_maintenance_page_and_is_audited(site: Site) -> None:
    await maintenance(site).switch_on_host("host-cli", MaintenanceReason.OTHER)
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        "/admin/groups/demo/settings", data={"name": "Nieuw"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 503
    assert "Onderhoudsmodus: alleen lezen" in response.text
    log = (await site.client.get("/admin/audit")).text
    assert "Geweigerd: onderhoudsmodus" in log
    assert "Nieuw" not in log


@pytest.mark.parametrize("settings", [{"maintenance_mode": True}], indirect=True)
async def test_the_environment_override_cannot_be_switched_off(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/maintenance")).text
    assert "Aangezet via de server" in page
    assert "MAINTENANCE_MODE" in page

    response = await site.client.post("/admin/maintenance/off", headers=SAME_ORIGIN)
    assert response.status_code == 409
    assert (await maintenance(site).status()).active
