"""System status and Advanced settings, read-only (ADR 0042 §11, build step 3)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import PlatformSettingRow, SettingsVersionRow
from app.admin.settings_routes import _FIELD_LABELS
from app.domain.authorization import Principal
from app.domain.privacy import PII_FIELDS
from app.web.protection import booking_limit
from tests.admin.conftest import AUDITOR, ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.principals import principal

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


async def test_the_rc_channel_in_a_development_installation_is_listed_not_fatal(site: Site) -> None:
    """ADR 0067: choosing rc outside production shows what production would refuse; no crash."""
    await site.login_as(SUPER)

    saved = await site.client.post(
        "/admin/settings/update_channel", data={"value": "rc"}, headers=SAME_ORIGIN
    )
    page = await site.client.get("/admin/settings")

    assert saved.status_code == 303
    assert page.status_code == 200
    assert "het updatekanaal rc mag in productie niet gekozen zijn" in page.text


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


# Changing (ADR 0042 step 4).


async def test_the_superuser_changes_a_setting_and_it_applies_at_once(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await site.client.post(
        "/admin/settings/admin_session_hours", data={"value": "4"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/settings?done=saved")
    page = (await site.client.get("/admin/settings")).text
    assert app.state.settings.admin_session_hours == 4
    assert 'action="/admin/settings/admin_session_hours/reset"' in page


async def test_a_value_outside_the_bounds_is_refused_at_its_row(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        "/admin/settings/admin_session_hours", data={"value": "99"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'id="error-admin_session_hours"' in response.text
    assert "past niet binnen de grenzen van het portaal" in response.text


async def test_reset_brings_back_the_default(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await site.client.post(
        "/admin/settings/admin_session_hours", data={"value": "4"}, headers=SAME_ORIGIN
    )

    response = await site.client.post(
        "/admin/settings/admin_session_hours/reset", headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    await site.client.get("/admin/settings")
    assert app.state.settings.admin_session_hours == 8


async def test_rate_limits_follow_a_change_without_a_restart(site: Site) -> None:
    await site.login_as(SUPER)

    await site.client.post(
        "/admin/settings/rate_limit_bookings", data={"value": "5/hour"}, headers=SAME_ORIGIN
    )
    await site.client.get("/admin/settings")

    assert booking_limit() == "5/hour"


async def test_the_auditor_sees_no_forms_and_may_not_post(site: Site) -> None:
    await site.login_as(AUDITOR)

    page = (await site.client.get("/admin/settings")).text
    response = await site.client.post(
        "/admin/settings/admin_session_hours", data={"value": "4"}, headers=SAME_ORIGIN
    )

    assert 'action="/admin/settings/' not in page
    assert response.status_code == 403


@pytest.mark.usefixtures("lock_session_hours")
async def test_a_locked_setting_has_no_form(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/settings")).text

    assert 'action="/admin/settings/admin_session_hours"' not in page
    assert 'action="/admin/settings/admin_reauth_minutes"' in page


# Secrets and taking over from the environment (ADR 0042 step 6).

SECRET = "never-shown-anywhere"  # noqa: S105 - test value


async def test_a_secret_is_written_but_never_shown(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await site.client.post(
        "/admin/settings/smtp_password", data={"value": SECRET}, headers=SAME_ORIGIN
    )
    page = (await site.client.get("/admin/settings")).text

    assert response.status_code == 303
    assert SECRET not in page
    assert 'type="password"' in page
    assert app.state.settings.smtp_password.get_secret_value() == SECRET


async def test_an_empty_secret_keeps_the_current_one(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await site.client.post(
        "/admin/settings/smtp_password", data={"value": SECRET}, headers=SAME_ORIGIN
    )

    response = await site.client.post(
        "/admin/settings/smtp_password", data={"value": ""}, headers=SAME_ORIGIN
    )
    await site.client.get("/admin/settings")

    assert "done=unchanged" in response.headers["location"]
    assert app.state.settings.smtp_password.get_secret_value() == SECRET


@pytest.mark.usefixtures("lock_session_hours")
async def test_a_locked_value_is_taken_over_and_names_the_variable_to_remove(
    site: Site,
) -> None:
    await site.login_as(SUPER)

    before = (await site.client.get("/admin/settings")).text
    response = await site.client.post(
        "/admin/settings/admin_session_hours/adopt", headers=SAME_ORIGIN
    )
    after = (await site.client.get("/admin/settings")).text

    assert 'action="/admin/settings/admin_session_hours/adopt"' in before
    assert response.status_code == 303
    assert "Overgenomen in het portaal" in after
    assert 'action="/admin/settings/admin_session_hours/adopt"' not in after


# The login mapping, saved only after a dry run (ADR 0042 step 7, §8).

LOGIN_FORM = {
    "oidc_role_claim": "realm_roles",
    "oidc_organisation_claim": "organisations",
    "oidc_group_claim": "groups",
    "oidc_role_superuser": "op-root",
    "oidc_role_org_admin": "op-org",
    "oidc_role_group_admin": "op-group",
    "oidc_role_auditor": "op-audit",
    "oidc_role_agent": "op-agent",
    "oidc_entitlement_claim": "op_entitlements",
    "oidc_entitlement_prefix": "op-acc",
}


async def _try(site: Site, trial: Principal) -> str:
    """Propose ``LOGIN_FORM``, log in again with ``trial`` as the dry run's verdict."""
    response = await site.client.post(
        "/admin/settings/login/test", data=LOGIN_FORM, headers=SAME_ORIGIN
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?next=/admin/settings&fresh=1"
    site.idp.trial_principal = trial
    start = await site.client.get(response.headers["location"])
    assert start.status_code == 303
    done = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})
    return done.headers["location"]


async def test_a_mapping_that_keeps_the_superuser_is_saved_after_a_fresh_login(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    location = await _try(site, SUPER)

    assert location == "/admin/settings?done=login_mapping_saved"
    assert site.idp.fresh[-1] is True
    await site.client.get("/admin/settings")
    assert app.state.settings.oidc_role_claim == "realm_roles"


async def test_a_mapping_that_would_lock_out_is_not_saved(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    nobody = principal("sub-super", "Sanne Super", frozenset(), frozenset(), frozenset())

    location = await _try(site, nobody)

    assert location == "/admin/settings?done=login_mapping_refused"
    await site.client.get("/admin/settings")
    assert app.state.settings.oidc_role_claim == "roles"


async def test_an_invalid_mapping_is_refused_before_any_login(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        "/admin/settings/login/test",
        data={**LOGIN_FORM, "oidc_role_auditor": "op-root"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert 'id="error-login-oidc_role_auditor"' in response.text


async def test_only_the_superuser_sees_and_posts_the_login_mapping(site: Site) -> None:
    await site.login_as(AUDITOR)

    page = (await site.client.get("/admin/settings")).text
    response = await site.client.post(
        "/admin/settings/login/test", data=LOGIN_FORM, headers=SAME_ORIGIN
    )

    assert 'action="/admin/settings/login/test"' not in page
    assert response.status_code == 403


async def test_a_proposal_older_than_the_reauth_limit_is_dropped(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    response = await site.client.post(
        "/admin/settings/login/test", data=LOGIN_FORM, headers=SAME_ORIGIN
    )
    site.clock.advance(timedelta(minutes=app.state.settings.admin_reauth_minutes + 1))
    site.idp.trial_principal = SUPER
    await site.client.get(response.headers["location"])

    done = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})
    await site.client.get("/admin/settings")

    assert "login_mapping" not in done.headers["location"]
    assert app.state.settings.oidc_role_claim == "roles"


async def test_a_webex_service_app_set_in_the_portal_wires_the_integrations(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    assert app.state.integrations.video is None

    for key, value in (
        ("webex_client_id", "client-1"),
        ("webex_client_secret", "client-secret"),
        ("webex_refresh_token", "refresh-token"),
    ):
        response = await site.client.post(
            f"/admin/settings/{key}", data={"value": value}, headers=SAME_ORIGIN
        )
        assert response.status_code == 303, key
    page = (await site.client.get("/admin/settings")).text

    assert app.state.integrations.video is not None
    assert app.state.call_service is not None
    assert "client-secret" not in page
    assert "refresh-token" not in page


async def test_the_data_fields_card_is_read_only_and_links_to_the_manual(site: Site) -> None:
    """ADR 0063 §6: the auditor sees the fixed list with its classification; nobody edits it."""
    await site.login_as(AUDITOR)

    text = (await site.client.get("/admin/settings")).text
    manual = await site.client.get("/admin/help/data-fields")

    assert '<h2 id="data-fields-heading">Gegevensvelden</h2>' in text
    for field in PII_FIELDS:
        assert f'<code class="muted">{field}</code>' in text
    assert text.count("Direct: identificeert de burger op zichzelf") == 4
    assert text.count("Gevoelig: wat de burger ons vertelde") == 5
    assert "ligt vast in de software" in text
    assert 'href="/admin/help/data-fields"' in text
    card = text.split('id="data-fields"')[1].split("</section>")[0]
    assert "<form" not in card  # read-only, for every role
    assert manual.status_code == 200
    assert "Gegevensvelden en persoonsgegevens" in manual.text


def test_every_personal_data_field_has_a_card_label() -> None:
    assert set(_FIELD_LABELS) == set(PII_FIELDS)


async def test_system_status_shows_the_latest_certificate_check(site: Site) -> None:
    # ADR 0064: what the worker found, in words, with issuer and days left.
    from app.adapters.db.uow import SqlUnitOfWork
    from app.domain.tls import Certificate, CertificateCheck, Finding, Trust

    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    certificate = Certificate(
        "afspraak.example.nl",
        "Let's Encrypt (R11)",
        site.clock.now() - timedelta(days=30),
        site.clock.now() + timedelta(days=59, hours=12),
        ("afspraak.example.nl",),
    )
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        await tx.save_certificate_check(
            CertificateCheck(
                site.clock.now(), "afspraak.example.nl", Finding.OK, certificate, Trust.TRUSTED
            )
        )
    await site.login_as(AUDITOR)

    text = (await site.client.get("/admin/settings")).text

    assert "Certificaat van het publieke adres" in text
    assert "<code>afspraak.example.nl</code>" in text
    assert "Let&#39;s Encrypt (R11)" in text or "Let's Encrypt (R11)" in text
    assert "nog 59 dagen" in text
    assert "chip--success" in text
    assert "In orde: het certificaat wordt vertrouwd" in text


async def test_system_status_without_a_check_says_so(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/settings")).text

    assert "Nog niet gecontroleerd" in text
    assert (
        "chip--success"
        not in text.split("Certificaat van het publieke adres")[1].split("</section>")[0]
    )
