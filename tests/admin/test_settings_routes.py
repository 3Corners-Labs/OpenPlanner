"""System status and Advanced settings, read-only (ADR 0042 §11, build step 3)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import AuditEventRow, PlatformSettingRow, SettingsVersionRow
from app.admin.settings_routes import _FIELD_LABELS
from app.domain.authorization import Principal
from app.domain.privacy import PII_FIELDS
from app.web.protection import booking_limit, unknown_link_limit
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

    text = (await site.client.get("/admin/settings")).text + (
        await site.client.get("/admin/settings/values")
    ).text

    assert '<h1 id="page-title">Geavanceerde instellingen</h1>' in text
    assert "Systeemstatus" in text
    assert "<code>SETTINGS_ENCRYPTION_KEY</code>" in text
    assert "Sessieduur beheerportaal" in text  # a class C label
    assert "Standaardwaarde" in text  # its source
    assert 'href="/admin/settings"' in text  # in the navigation


async def test_secrets_and_bootstrap_values_are_never_shown(site: Site) -> None:
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/settings")).text + (
        await site.client.get("/admin/settings/values")
    ).text

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

    text = (await site.client.get("/admin/settings/values")).text
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

    text = (await site.client.get("/admin/settings/values")).text
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
    assert response.headers["location"].startswith("/admin/settings/values?done=saved")
    page = (await site.client.get("/admin/settings/values")).text
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


async def test_the_unknown_link_bound_follows_a_change_and_keeps_its_bounds(site: Site) -> None:
    await site.login_as(SUPER)

    await site.client.post(
        "/admin/settings/rate_limit_unknown_links", data={"value": "30/hour"}, headers=SAME_ORIGIN
    )
    await site.client.post(
        "/admin/settings/rate_limit_unknown_links", data={"value": "500/hour"}, headers=SAME_ORIGIN
    )
    await site.client.get("/admin/settings")

    assert unknown_link_limit() == "30/hour"


async def test_the_auditor_sees_no_forms_and_may_not_post(site: Site) -> None:
    await site.login_as(AUDITOR)

    page = (await site.client.get("/admin/settings/values")).text
    response = await site.client.post(
        "/admin/settings/admin_session_hours", data={"value": "4"}, headers=SAME_ORIGIN
    )

    assert 'action="/admin/settings/' not in page
    assert response.status_code == 403


@pytest.mark.usefixtures("lock_session_hours")
async def test_a_locked_setting_has_no_field(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/settings/values")).text

    assert 'name="admin_session_hours"' not in page
    assert 'name="admin_reauth_minutes"' in page


# Secrets and taking over from the environment (ADR 0042 step 6).

SECRET = "never-shown-anywhere"  # noqa: S105 - test value


async def test_a_secret_is_written_but_never_shown(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await site.client.post(
        "/admin/settings/smtp_password", data={"value": SECRET}, headers=SAME_ORIGIN
    )
    page = (await site.client.get("/admin/settings/values")).text

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

    before = (await site.client.get("/admin/settings/values")).text
    response = await site.client.post(
        "/admin/settings/admin_session_hours/adopt", headers=SAME_ORIGIN
    )
    after = (await site.client.get("/admin/settings/values")).text

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
    assert response.headers["location"] == "/admin/login?next=/admin/settings/values&fresh=1"
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

    assert location == "/admin/settings/values?done=login_mapping_saved"
    assert site.idp.fresh[-1] is True
    await site.client.get("/admin/settings")
    assert app.state.settings.oidc_role_claim == "realm_roles"


async def test_a_mapping_that_would_lock_out_is_not_saved(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    nobody = principal("sub-super", "Sanne Super", frozenset(), frozenset(), frozenset())

    location = await _try(site, nobody)

    assert location == "/admin/settings/values?done=login_mapping_refused"
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

    page = (await site.client.get("/admin/settings/values")).text
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
    page = (await site.client.get("/admin/settings/values")).text

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
    assert text.count("Direct: identificeert de deelnemer op zichzelf") == 4
    assert text.count("Gevoelig: wat de deelnemer ons vertelde") == 5
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


# One Save per topic: only the values that differ are saved, the first refusal shows at its row.


async def test_each_topic_is_one_form_with_one_save(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/settings/values")).text

    assert 'action="/admin/settings/topic/admin_session"' in page
    assert 'formaction="/admin/settings/' not in page  # nothing stored yet: no reset buttons
    assert 'name="value"' not in page  # fields carry the setting's key
    assert 'name="admin_session_hours"' in page
    assert 'name="product_footer"' in page  # on/off as a choice
    assert 'value="true"' in page


async def test_the_topic_form_saves_only_what_differs(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await site.client.post(
        "/admin/settings/topic/admin_session",
        data={
            "admin_session_hours": "4",
            "admin_reauth_minutes": str(app.state.settings.admin_reauth_minutes),
        },
        headers=SAME_ORIGIN,
    )
    page = (await site.client.get("/admin/settings/values")).text

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/values?done=saved#topic-admin_session"
    assert app.state.settings.admin_session_hours == 4
    assert 'formaction="/admin/settings/admin_session_hours/reset"' in page
    assert 'formaction="/admin/settings/admin_reauth_minutes/reset"' not in page


async def test_the_topic_form_with_nothing_new_changes_nothing(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await site.client.post(
        "/admin/settings/topic/admin_session",
        data={"admin_session_hours": str(app.state.settings.admin_session_hours)},
        headers=SAME_ORIGIN,
    )

    assert (
        response.headers["location"] == "/admin/settings/values?done=unchanged#topic-admin_session"
    )


async def test_the_topic_form_stops_at_the_first_refusal(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await site.client.post(
        "/admin/settings/topic/admin_session",
        data={"admin_session_hours": "99", "admin_reauth_minutes": "5"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    assert 'id="error-admin_session_hours"' in response.text
    assert app.state.settings.admin_session_hours == 8


async def test_the_topic_form_sets_a_boolean_and_an_empty_secret_keeps_the_current(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await site.client.post(
        "/admin/settings/smtp_password", data={"value": SECRET}, headers=SAME_ORIGIN
    )

    response = await site.client.post(
        "/admin/settings/topic/mail",
        data={"smtp_password": "", "smtp_tls": "true"},
        headers=SAME_ORIGIN,
    )
    await site.client.get("/admin/settings")

    assert response.headers["location"] == "/admin/settings/values?done=saved#topic-mail"
    assert app.state.settings.smtp_tls is True
    assert app.state.settings.smtp_password.get_secret_value() == SECRET


# ADR 0089: the configuration package block.
PACKAGE_PASSPHRASE = "a passphrase of sixteen chars"  # noqa: S105 - a test passphrase


async def _export(site: Site) -> bytes:
    response = await site.client.post(
        "/admin/settings/package/export",
        data={"passphrase": PACKAGE_PASSPHRASE, "passphrase_repeat": PACKAGE_PASSPHRASE},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.content


async def test_the_superuser_exports_a_package_and_imports_it_again(site: Site) -> None:
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/settings/package")).text
    assert 'id="package-heading"' in page

    package = await _export(site)

    assert package.startswith(b"SQLite format 3\x00")
    response = await site.client.post(
        "/admin/settings/package/import",
        data={"passphrase": PACKAGE_PASSPHRASE, "confirm": "yes"},
        files={"package": ("config.sqlite", package, "application/vnd.sqlite3")},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 303, response.text
    assert (
        response.headers["location"]
        == "/admin/settings/package?done=package_imported&count=2#package-heading"
    )
    page = (await site.client.get(response.headers["location"])).text
    assert "Het configuratiepakket is geïmporteerd: 2 organisatie(s)." in page
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        rows = list(await session.scalars(select(AuditEventRow).order_by(AuditEventRow.id)))
    details = [row.detail for row in rows if row.detail.startswith("configuration.")]
    assert details == [
        "configuration.export",
        "configuration.import,organisations:2,groups:2,identity_providers:0,api_clients:0,"
        "secrets:0",
    ]
    assert all(PACKAGE_PASSPHRASE not in row.detail + row.reason for row in rows)


async def test_a_mismatched_or_short_passphrase_refuses_the_export(site: Site) -> None:
    await site.login_as(SUPER)

    mismatch = await site.client.post(
        "/admin/settings/package/export",
        data={"passphrase": PACKAGE_PASSPHRASE, "passphrase_repeat": "something else"},
        headers=SAME_ORIGIN,
    )
    short = await site.client.post(
        "/admin/settings/package/export",
        data={"passphrase": "short", "passphrase_repeat": "short"},
        headers=SAME_ORIGIN,
    )

    assert mismatch.status_code == 422
    assert short.status_code == 422
    assert 'id="error-package" tabindex="-1" autofocus' in short.text  # opens at the error
    assert "minstens 16 tekens" in short.text


async def test_an_import_without_the_confirmation_or_with_rubbish_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    unconfirmed = await site.client.post(
        "/admin/settings/package/import",
        data={"passphrase": PACKAGE_PASSPHRASE},
        files={
            "package": ("config.sqlite", b"SQLite format 3\x00rubbish", "application/octet-stream")
        },
        headers=SAME_ORIGIN,
    )
    rubbish = await site.client.post(
        "/admin/settings/package/import",
        data={"passphrase": PACKAGE_PASSPHRASE, "confirm": "yes"},
        files={"package": ("config.sqlite", b"not a database", "application/octet-stream")},
        headers=SAME_ORIGIN,
    )

    assert unconfirmed.status_code == 422
    assert rubbish.status_code == 422
    assert "geen configuratiepakket van OpenPlanner" in rubbish.text


async def test_the_auditor_sees_no_package_block(site: Site) -> None:
    await site.login_as(AUDITOR)

    page = (await site.client.get("/admin/settings")).text

    assert 'id="package-heading"' not in page
    assert 'href="/admin/settings/package"' not in page  # ADR 0101: no sub-tab without the right
    assert (await site.client.get("/admin/settings/package")).status_code == 403


async def test_advanced_settings_shows_its_sub_tabs_and_a_table_of_contents(site: Site) -> None:
    """ADR 0101: three sub-tabs, the current one marked; "On this page" lists every topic."""
    await site.login_as(SUPER)

    status = (await site.client.get("/admin/settings")).text
    values = (await site.client.get("/admin/settings/values")).text

    assert '<a href="/admin/settings" aria-current="page"><span>Systeemstatus</span></a>' in status
    assert '<a href="/admin/settings/values"><span>Instellingen</span></a>' in status
    assert '<a href="/admin/settings/package"><span>Configuratiepakket</span></a>' in status
    assert 'id="topic-' not in status
    assert 'id="package-heading"' not in values
    assert "Op deze pagina" in values
    for topic in re.findall(r'<h2 id="(topic-[a-z_]+)">', values):
        assert f'href="#{topic}"' in values, topic
