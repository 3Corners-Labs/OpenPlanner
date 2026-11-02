"""The overview as a grid of widgets without script (ADR 0061 step 1, OV-2)."""

import re
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import JobRow
from app.domain.audit import AuditEvent, Outcome
from app.domain.calls import CallStatus
from app.web.templates import HTML_CSP
from tests.admin.conftest import (
    AGENT,
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.support.queue import own_queue, site_uow

WIDGET = re.compile(r'<section class="widget[^"]*" data-widget="([a-z-]+)"')


def _widgets(html: str) -> list[str]:
    return WIDGET.findall(html)


async def test_the_overview_renders_the_visible_widgets_of_the_role(site: Site) -> None:
    expected = {
        SUPER: ["queue", "bookings-today", "changes", "tasks", "status", "rights"],
        ORG_ADMIN: ["bookings-today", "queue", "changes", "tasks", "rights"],
        GROUP_ADMIN: ["bookings-today", "queue", "changes", "rights"],
        AUDITOR: ["changes", "status", "rights"],
        AGENT: ["queue", "rights"],
    }
    for principal, keys in expected.items():
        await site.login_as(principal)
        response = await site.client.get("/admin/")

        assert response.status_code == 200
        assert _widgets(response.text) == keys, principal.display_name


async def test_the_overview_works_without_script(site: Site) -> None:
    await site.login_as(SUPER)
    response = await site.client.get("/admin/")

    assert response.headers["content-security-policy"] == HTML_CSP
    assert "<script" not in response.text
    assert 'class="widget-grid"' in response.text
    assert 'href="/admin/"' in response.text  # the Refresh link
    assert "Vernieuwen" in response.text


async def test_the_group_cards_stay_below_the_grid(site: Site) -> None:
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/")).text

    assert text.index('class="widget-grid"') < text.index('href="/admin/groups/demo"')


async def test_a_fragment_renders_without_the_shell(site: Site) -> None:
    await site.login_as(SUPER)
    response = await site.client.get("/admin/overview/w/rights")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == HTML_CSP
    assert _widgets(response.text) == ["rights"]
    assert "<html" not in response.text
    assert 'class="admin-shell' not in response.text


async def test_a_widget_the_user_may_not_see_is_refused_and_not_rendered(site: Site) -> None:
    await site.login_as(AGENT)

    assert (await site.client.get("/admin/overview/w/changes")).status_code == 403
    assert (await site.client.get("/admin/overview/w/bookings-today")).status_code == 403
    assert (await site.client.get("/admin/overview/w/status")).status_code == 403
    assert "changes" not in _widgets((await site.client.get("/admin/")).text)
    assert "status" not in _widgets((await site.client.get("/admin/")).text)
    await site.login_as(AUDITOR)
    assert (await site.client.get("/admin/overview/w/queue")).status_code == 403
    assert "queue" not in _widgets((await site.client.get("/admin/")).text)


async def test_a_widget_that_is_not_available_yet_has_no_route(site: Site) -> None:
    await site.login_as(SUPER)

    assert (await site.client.get("/admin/overview/w/alerts")).status_code == 404


async def test_the_rights_widget_shows_roles_and_permissions_without_values(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    text = (await site.client.get("/admin/overview/w/rights")).text

    assert "Groepsbeheerder" in text
    assert 'href="/admin/rights"' in text


async def test_the_bookings_widget_counts_per_group_in_scope(site: Site) -> None:
    await site.login_as(OUTSIDER)
    text = (await site.client.get("/admin/overview/w/bookings-today")).text

    assert "Andere" in text
    assert 'href="/admin/groups/andere-team/agenda"' in text
    # The link's own name: the group card below the grid links the same text elsewhere (2.4.4).
    assert 'aria-label="Agenda van Andere: team"' in text
    assert "/admin/groups/demo/agenda" not in text


async def test_the_bookings_widget_shows_a_count_per_group(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    text = (await site.client.get("/admin/overview/w/bookings-today")).text

    assert "Demo: videogesprek" in text
    assert 'class="widget__count"' in text


async def test_the_changes_widget_lists_only_successful_changes_in_scope(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = app.state.admin_service._uow  # noqa: SLF001
    now = site.clock.now()
    async with uow.locked() as tx:
        await tx.add_audit_event(
            AuditEvent(now, "sub-group", "group.edit", Outcome.SUCCESS, "", "demo", "demo", "name")
        )
        await tx.add_audit_event(
            AuditEvent(now, "sub-super", "admin.login", Outcome.SUCCESS, "", None, None, "")
        )
        await tx.add_audit_event(
            AuditEvent(
                now, "sub-outsider", "group.edit", Outcome.SUCCESS, "", "andere", "andere-team"
            )
        )
        await tx.add_audit_event(
            AuditEvent(
                now, "sub-group", "organisation.manage", Outcome.DENIED, "not_permitted", "demo"
            )
        )
        await tx.add_audit_event(
            AuditEvent(now, "sub-group", "group.view", Outcome.SUCCESS, "", "demo", "demo")
        )
    await site.login_as(GROUP_ADMIN)
    text = (await site.client.get("/admin/overview/w/changes")).text

    assert text.count('class="widget-list__item"') == 1
    assert "demo / demo" in text
    assert "andere" not in text
    assert 'href="/admin/audit"' in text


async def test_the_changes_widget_has_an_empty_state(site: Site) -> None:
    await site.login_as(OUTSIDER)
    text = (await site.client.get("/admin/overview/w/changes")).text

    assert 'class="widget__empty"' in text
    assert "Nog geen wijzigingen" in text


async def test_no_pii_in_any_fragment(site: Site) -> None:
    """Counts, group names and times only (ADR 0061 §8)."""
    await site.login_as(SUPER)
    for key in ("rights", "bookings-today", "changes", "queue", "tasks", "status"):
        text = (await site.client.get(f"/admin/overview/w/{key}")).text
        assert "@" not in text, key
        assert "example.org" not in text, key


async def test_the_queue_widget_has_an_empty_state_without_an_own_queue(site: Site) -> None:
    await site.login_as(AGENT)
    text = (await site.client.get("/admin/overview/w/queue")).text

    assert 'class="widget__empty"' in text
    assert "Geen eigen wachtrij in uw bereik." in text


async def test_the_queue_widget_counts_the_waiting_participant_and_the_agent_signed_in(
    site: Site,
) -> None:
    booking_id = await own_queue(site)
    async with site_uow(site).locked() as tx:
        await tx.save_call_status(
            CallStatus(
                booking_id, participants=1, updated_at=site.clock.now() - timedelta(minutes=3)
            )
        )
    await site.login_as(AGENT)
    text = (await site.client.get("/admin/overview/w/queue")).text

    assert 'href="/admin/groups/demo/queue"' in text
    assert 'aria-label="Wachtrij van Demo: videogesprek"' in text
    assert '<td class="widget-table__number">1</td>' in text  # waiting, and the agent signed in
    assert "3 min" in text
    assert "Ada" not in text
    assert "ada@example.org" not in text
    assert "webex.example" not in text


async def test_the_queue_widget_does_not_list_a_group_outside_the_reach(site: Site) -> None:
    await own_queue(site)
    await site.login_as(OUTSIDER)
    text = (await site.client.get("/admin/overview/w/queue")).text

    assert "demo" not in text
    assert "Geen eigen wachtrij in uw bereik." in text


async def test_the_tasks_widget_has_an_empty_state(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    text = (await site.client.get("/admin/overview/w/tasks")).text

    assert "Geen lopende of mislukte taken." in text


async def test_the_tasks_widget_lists_running_and_failed_jobs_without_their_booking(
    site: Site,
) -> None:
    await own_queue(site)
    async with site_uow(site).locked() as tx:
        jobs = await tx.active_jobs(5)
    assert jobs.total == 0
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session, session.begin():
        await session.execute(
            update(JobRow)
            .where(JobRow.type == "create_video_room")
            .values(status="running", claimed_at=site.clock.now(), attempts=2)
        )
        await session.execute(
            update(JobRow)
            .where(JobRow.type == "send_confirmation")
            .values(status="failed", last_error="Ada Lovelace ada@example.org")
        )
    await site.login_as(AUDITOR)
    text = (await site.client.get("/admin/overview/w/tasks")).text

    assert "Videogesprek aanmaken" in text
    assert "Bezig" in text
    assert "Mislukt" in text
    assert "poging 2" in text
    assert "Ada" not in text
    assert "ada@example.org" not in text
    assert "booking" not in text.lower()


async def test_the_status_widget_tells_version_update_maintenance_worker_and_environment(
    site: Site,
) -> None:
    await site.login_as(AUDITOR)
    text = (await site.client.get("/admin/overview/w/status")).text

    for word in ("Versie", "Update", "Onderhoud", "Achtergrondproces", "Diagnosemodus", "Omgeving"):
        assert word in text
    assert "Geen hartslag gevonden" in text  # the test app runs no worker
    assert 'href="/admin/settings"' in text
    assert "/admin/updates" not in text  # the auditor may not open the updates page
    await site.login_as(SUPER)
    assert 'href="/admin/updates"' in (await site.client.get("/admin/overview/w/status")).text


async def test_the_status_widget_shows_maintenance_and_a_fresh_worker_heartbeat(
    site: Site,
) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    heartbeat = app.state.settings.worker_heartbeat_path
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(site.clock.now().isoformat())
    await site.login_as(SUPER)
    await site.client.post("/admin/maintenance/on", data={"reason": "update"}, headers=SAME_ORIGIN)
    text = (await site.client.get("/admin/overview/w/status")).text

    assert "Aan sinds" in text
    assert "Actief," in text


async def test_the_status_widget_reports_mail_without_tls_in_production_adr_0091(
    site: Site,
) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    current = app.state.settings
    await site.login_as(SUPER)
    plain = {"environment": "prod", "smtp_host": "relay.demo.nl", "smtp_starttls": False}
    app.state.settings = current.model_copy(update=plain)
    try:
        text = (await site.client.get("/admin/overview/w/status")).text
        assert "zonder TLS" in text
        assert 'href="/admin/integrations"' in text
        app.state.settings = current.model_copy(
            update={**plain, "smtp_plaintext_relay_allowed": True}
        )
        assert "zonder TLS" not in (await site.client.get("/admin/overview/w/status")).text
    finally:
        app.state.settings = current


async def test_the_status_widget_warns_about_a_proxy_hop_without_tls_adr_0091(
    site: Site,
) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    current = app.state.settings
    await site.login_as(SUPER)
    warning = "zonder TLS naar web"
    try:
        app.state.settings = current.model_copy(update={"forwarded_allow_ips": "172.31.240.2"})
        assert warning not in (await site.client.get("/admin/overview/w/status")).text
        remote = {"forwarded_allow_ips": "10.0.0.5"}
        app.state.settings = current.model_copy(update=remote)
        assert warning in (await site.client.get("/admin/overview/w/status")).text
        files = {"web_tls_cert_file": "/web-tls/c.pem", "web_tls_key_file": "/web-tls/k.pem"}
        app.state.settings = current.model_copy(update={**remote, **files})
        assert warning not in (await site.client.get("/admin/overview/w/status")).text
    finally:
        app.state.settings = current
