"""Alerts in the portal (ADR 0061 §7, OV-6): banner, closing, the widget, live refresh, pop-ups."""

import re
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from app.admin import alerts
from app.admin.address_view import FINDING_LABELS as ADDRESS_FINDING_LABELS
from app.admin.audit_routes import REASON_LABELS
from app.config import Settings
from app.domain.alerts import QUEUE_WAIT_EVENT
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.calls import CallStatus
from app.domain.integration_check import CHECK_EVENT
from app.domain.storage import STORAGE_EVENT
from app.i18n.formatting import placeholders
from app.web.settings_labels import TLS_FINDING_LABELS
from app.web.templates import HTML_CSP, SCRIPT_CSP
from tests.admin.conftest import AGENT, AUDITOR, ORG_ADMIN, OUTSIDER, SUPER, Site, _running_site
from tests.support.admin_site import site_uow
from tests.support.queue import own_queue

BANNER = 'class="alert alert-banner"'
POLL = re.compile(r'data-poll="([^"]+)"')


async def _condition(
    site: Site,
    action: str,
    outcome: Outcome,
    reason: str = "",
    *,
    organisation: str | None = None,
    group: str | None = None,
    detail: str = "",
    minutes: int = 0,
) -> None:
    async with site_uow(site).locked() as tx:
        await tx.add_audit_event(
            AuditEvent(
                site.clock.now() + timedelta(minutes=minutes),
                SYSTEM_ACTOR,
                action,
                outcome,
                reason,
                organisation,
                group,
                detail,
                Source.SYSTEM,
                trace_id="a" * 32,
            )
        )


async def _disk_full(site: Site, minutes: int = 0) -> None:
    await _condition(
        site,
        STORAGE_EVENT,
        Outcome.FAILURE,
        "free_space_critical",
        detail="free_percent:4,database_mb:1",
        minutes=minutes,
    )


async def test_an_outage_shows_as_a_banner_on_every_admin_page(site: Site) -> None:
    await _disk_full(site)
    await site.login_as(SUPER)

    for path in ("/admin/", "/admin/audit", "/admin/groups/demo"):
        text = (await site.client.get(path)).text
        assert BANNER in text, path
        assert "Storing" in text, path  # the level in words, not only in colour
        assert "De schijfruimte voor de gegevens is bijna op." in text, path


async def test_the_banner_links_to_the_chain_of_the_log_entry(site: Site) -> None:
    await _disk_full(site)
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/groups/demo")).text

    assert 'href="/admin/audit?trace=' in text.split(BANNER)[1].split("</section>")[0]


async def test_a_platform_outage_is_not_shown_outside_the_platform_reach(site: Site) -> None:
    await _disk_full(site)
    for principal in (ORG_ADMIN, AGENT, OUTSIDER):
        await site.login_as(principal)
        assert BANNER not in (await site.client.get("/admin/")).text, principal.display_name


async def test_a_warning_is_not_a_banner_but_is_in_the_widget(site: Site) -> None:
    await _condition(site, STORAGE_EVENT, Outcome.FAILURE, "free_space_low")
    await site.login_as(SUPER)

    assert BANNER not in (await site.client.get("/admin/")).text
    widget = (await site.client.get("/admin/overview/w/alerts")).text
    assert "Waarschuwing" in widget
    assert "Er is weinig schijfruimte over voor de gegevens." in widget


async def test_a_recovered_condition_leaves_the_banner(site: Site) -> None:
    await _disk_full(site)
    await _condition(site, STORAGE_EVENT, Outcome.SUCCESS, minutes=1)
    await site.login_as(SUPER)

    assert BANNER not in (await site.client.get("/admin/")).text
    assert "Geen open meldingen." in (await site.client.get("/admin/overview/w/alerts")).text


async def test_closing_hides_this_streak_and_a_new_one_shows_again(site: Site) -> None:
    await _disk_full(site)
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/audit")).text
    [token] = re.findall(r'name="token" value="([0-9a-f]{16})"', page)

    closed = await site.client.get(
        "/admin/alerts/close", params={"token": token, "next": "/admin/audit"}
    )
    assert closed.status_code == 303
    assert closed.headers["location"] == "/admin/audit"
    assert BANNER not in (await site.client.get("/admin/audit")).text

    await _condition(site, STORAGE_EVENT, Outcome.SUCCESS, minutes=1)
    await _disk_full(site, minutes=2)
    assert BANNER in (await site.client.get("/admin/audit")).text


async def test_closing_ignores_a_malformed_token_and_a_foreign_next(site: Site) -> None:
    await _disk_full(site)
    await site.login_as(SUPER)

    closed = await site.client.get(
        "/admin/alerts/close", params={"token": "<b>", "next": "https://evil.example/"}
    )

    assert closed.headers["location"] == "/admin/"
    assert BANNER in (await site.client.get("/admin/")).text


async def test_the_banner_follows_the_profile_choice(site: Site) -> None:
    await _disk_full(site)
    await site.login_as(SUPER)
    await site.client.get(
        "/admin/preferences", params={"alert_prefs": "1", "alert_popups": "outages"}
    )

    assert BANNER not in (await site.client.get("/admin/")).text
    assert 'data-popup-level="outage"' in (await site.client.get("/admin/")).text


async def test_the_widget_filters_by_level(site: Site) -> None:
    await _disk_full(site)
    await _condition(site, "tls.certificate", Outcome.FAILURE, "expiring")
    await site.login_as(SUPER)

    every = (await site.client.get("/admin/overview/w/alerts")).text
    outages = (await site.client.get("/admin/overview/w/alerts?alerts-level=outage")).text

    assert every.count("data-alert-token=") == 2
    assert outages.count("data-alert-token=") == 1
    assert 'aria-current="true">Alleen storingen' in outages
    assert (
        await site.client.get("/admin/overview/w/alerts?alerts-level=x" + "y" * 20)
    ).status_code == 422


async def test_the_widget_draws_alerts_per_level_as_a_chart(site: Site) -> None:
    await _disk_full(site)
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/overview/w/alerts?view=bar")).text

    assert 'role="img"' in text
    assert "Storing: 1" in text


async def test_an_agent_sees_the_wait_alert_of_their_queue_with_a_link_to_it(site: Site) -> None:
    await _condition(
        site,
        QUEUE_WAIT_EVENT,
        Outcome.FAILURE,
        "wait_over_warning",
        organisation="demo",
        group="demo",
        detail="minutes:9",
    )
    await site.login_as(AGENT)
    text = (await site.client.get("/admin/overview/w/alerts")).text

    assert "Wachtrij van demo" in text
    assert "9 minuten" in text
    assert 'href="/admin/groups/demo/queue"' in text
    for principal in (AUDITOR, OUTSIDER):  # no queue.work on the group: not in reach
        await site.login_as(principal)
        text = (await site.client.get("/admin/overview/w/alerts")).text
        assert "Wachtrij van demo" not in text, principal.display_name


async def test_a_live_widget_answers_204_while_nothing_changed(site: Site) -> None:
    await site.login_as(SUPER)
    first = (await site.client.get("/admin/overview/w/alerts")).text
    [poll] = POLL.findall(first)
    poll = poll.replace("&amp;", "&")

    quiet = await site.client.get(poll)
    assert quiet.status_code == 204

    await _disk_full(site)
    changed = await site.client.get(poll)
    assert changed.status_code == 200
    assert "data-alert-token=" in changed.text
    assert "Storing" in changed.text


async def test_the_overview_loads_the_live_scripts_under_the_script_policy(site: Site) -> None:
    await site.login_as(SUPER)
    response = await site.client.get("/admin/")

    assert response.headers["content-security-policy"] == SCRIPT_CSP
    assert "live-region" in response.text
    assert "alert-popups" in response.text
    assert 'id="overview-announce"' in response.text
    assert response.text.count('aria-live="polite"') == 1  # one live region for the page
    assert 'data-popup-level="warning"' in response.text  # the default choice
    assert "data-poll=" in response.text
    assert '<section class="widget" data-widget="rights"' in response.text  # not live


@pytest.fixture
async def quiet_site(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    changed = settings.model_copy(update={"overview_live_enabled": False})
    async with _running_site(changed, admin_db) as running:
        yield running


async def test_with_live_off_the_overview_has_no_script(quiet_site: Site) -> None:
    await quiet_site.login_as(SUPER)
    response = await quiet_site.client.get("/admin/")

    assert response.headers["content-security-policy"] == HTML_CSP
    assert "<script" not in response.text
    assert "data-poll=" not in response.text


async def test_the_queue_widget_compares_the_wait_with_the_target_in_words(site: Site) -> None:
    booking_id = await own_queue(site)
    async with site_uow(site).locked() as tx:
        await tx.save_call_status(
            CallStatus(
                booking_id, participants=1, updated_at=site.clock.now() - timedelta(minutes=6)
            )
        )
    await site.login_as(AGENT)
    text = (await site.client.get("/admin/overview/w/queue")).text

    assert '<span class="chip chip--warning">boven het doel van 5 min</span>' in text


async def test_system_status_shows_the_storage(site: Site) -> None:
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/overview/w/status")).text

    assert "Opslag" in text
    assert "% vrij, database" in text


async def test_the_alerts_link_counts_the_open_alerts_for_the_screen_reader_too(
    site: Site,
) -> None:
    await _disk_full(site)
    await _condition(site, "tls.certificate", Outcome.FAILURE, "expiring")
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/audit?lang=en")).text

    assert 'aria-label="Alerts, 2 open"' in text


def test_every_alert_text_names_only_placeholders_the_page_provides() -> None:
    """A text with a placeholder nobody fills (`{base}` once) fails the whole overview."""
    keys = {
        *alerts.OWN_TEXTS.values(),
        *REASON_LABELS.values(),
        *TLS_FINDING_LABELS.values(),
        *ADDRESS_FINDING_LABELS.values(),
        alerts.OTHER_TEXT,
    }
    for locale in ("nl", "en"):
        catalogue = Path(f"app/i18n/locales/{locale}.po").read_text()
        texts = dict(re.findall(r'msgid "([^"]+)"\nmsgstr "((?:[^"\\\\]|\\\\.)*)"', catalogue))
        for key in keys:
            used = placeholders(texts[key])
            assert used <= alerts.ALERT_PARAMS, (locale, key, used)


@pytest.mark.parametrize(
    "code", ["webhooks.not_https", "webex.host_refused", "wxcc.branch.missing"]
)
async def test_a_failing_group_check_shows_as_an_alert_not_a_server_error(
    site: Site, code: str
) -> None:
    """docker01, 2026-11-03: the finding's own text needs its parameters, which an alert does not
    keep, and the overview answered 500 right after login."""
    await _condition(site, CHECK_EVENT, Outcome.FAILURE, code, organisation="demo", group="demo")
    await site.login_as(SUPER)

    overview = await site.client.get("/admin/")
    widget = await site.client.get("/admin/overview/w/alerts")

    assert overview.status_code == 200
    assert widget.status_code == 200
    assert f"({code})" in widget.text
    assert "Koppeling controleren" in widget.text
