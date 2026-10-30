"""The overview as a grid of widgets without script (ADR 0061 step 1, OV-2)."""

import re

from app.domain.audit import AuditEvent, Outcome
from app.web.templates import HTML_CSP
from tests.admin.conftest import (
    AGENT,
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SUPER,
    Site,
)

WIDGET = re.compile(r'<section class="widget[^"]*" data-widget="([a-z-]+)"')


def _widgets(html: str) -> list[str]:
    return WIDGET.findall(html)


async def test_the_overview_renders_the_visible_widgets_of_the_role(site: Site) -> None:
    expected = {
        SUPER: ["bookings-today", "changes", "rights"],
        ORG_ADMIN: ["bookings-today", "changes", "rights"],
        GROUP_ADMIN: ["bookings-today", "changes", "rights"],
        AUDITOR: ["changes", "rights"],
        AGENT: ["rights"],
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
    assert "changes" not in _widgets((await site.client.get("/admin/")).text)


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
    for key in ("rights", "bookings-today", "changes"):
        text = (await site.client.get(f"/admin/overview/w/{key}")).text
        assert "@" not in text, key
        assert "example.org" not in text, key
