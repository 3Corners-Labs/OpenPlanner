"""System status and Advanced settings, read-only (ADR 0042 §11, build step 3)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import PlatformSettingRow, SettingsVersionRow
from tests.admin.conftest import AUDITOR, ORG_ADMIN, SUPER, Site

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


async def _store(site: Site, **values: str) -> None:
    """Store portal values as step 4 will, and move the version counter."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session, session.begin():
        session.add_all(
            PlatformSettingRow(key=k, value=v, updated_at=NOW) for k, v in values.items()
        )
        await session.merge(SettingsVersionRow(id=1, version=1))


async def test_the_auditor_sees_the_status_and_every_portal_setting(site: Site) -> None:
    await site.login_as(AUDITOR)

    text = (await site.client.get("/admin/settings")).text

    assert '<h1 id="page-title">Geavanceerde instellingen</h1>' in text
    assert "Systeemstatus" in text
    assert "<code>SETTINGS_ENCRYPTION_KEY</code>" in text
    assert "Sessieduur beheerportaal" in text  # a class C label
    assert "Standaardwaarde" in text  # its source
    assert 'href="/admin/settings"' in text  # in the navigation


async def test_secrets_and_bootstrap_values_are_never_shown(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/settings")).text

    assert "x" * 32 not in text  # the session secret of the test settings
    assert "t" * 32 not in text  # the management token key
    assert "sqlite+aiosqlite" not in text  # a class B value


async def test_a_development_installation_shows_what_production_refuses(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/settings")).text

    assert "PUBLIC_BASE_URL moet in productie https gebruiken." in text


async def test_a_stored_portal_value_applies_without_a_restart(site: Site) -> None:
    await _store(site, admin_session_hours="4", rate_limit_bookings='"99/hour"')
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/settings")).text
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    assert "Beheerportaal" in text  # the portal as source
    assert app.state.settings.admin_session_hours == 4
    # Outside the bounds: ignored and reported, the default stays.
    assert "Opgeslagen waarde voldoet niet aan de regels" in text
    assert app.state.settings.rate_limit_bookings == "10/hour"


@pytest.fixture
def lock_session_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_SESSION_HOURS", "6")


@pytest.mark.usefixtures("lock_session_hours")
async def test_an_environment_lock_names_its_variable(site: Site) -> None:
    await _store(site, admin_session_hours="4")
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/settings")).text
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    assert "Vastgezet door de beheerder van de server: <code>ADMIN_SESSION_HOURS</code>" in text
    assert app.state.settings.admin_session_hours == 6  # the environment wins over the portal


async def test_an_org_admin_may_not_see_the_platform_settings(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    assert (await site.client.get("/admin/settings")).status_code == 403
