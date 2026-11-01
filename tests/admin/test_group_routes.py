"""Group and planning screens (WP7b): forms, confirmations, errors and fresh login."""

import re
from datetime import timedelta

import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events
from tests.support.participant import SAME_ORIGIN as PARTICIPANT
from tests.support.participant import confirm_newest

GROUP = "/admin/groups/demo"


async def _post(site: Site, path: str, data: dict[str, str]) -> object:
    return await site.client.post(path, data=data, headers=SAME_ORIGIN)


async def test_org_admin_creates_a_group_and_lands_on_it(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    form = (await site.client.get("/admin/groups/new")).text
    assert '<option value="demo" selected>Demo-gemeente</option>' in form
    assert "andere" not in form  # only organisations in the admin's scope are offered

    response = await site.client.post(
        "/admin/groups",
        data={
            "organisation": "demo",
            "name": "Vergunningen",
            "slug": "vergunningen",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "20",
        },
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/groups/vergunningen?done=created"
    page = (await site.client.get(response.headers["location"])).text
    assert 'role="status"' in page
    assert "De groep is aangemaakt" in page
    assert ("sub-org", "group.create", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]


async def test_time_zone_is_a_select_with_amsterdam_preselected(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    form = (await site.client.get("/admin/groups/new")).text

    assert (
        '<select id="timezone" name="timezone" required aria-describedby="timezone-hint">' in form
    )
    assert '<option value="Europe/Amsterdam" selected>Europe/Amsterdam</option>' in form
    assert '<option value="America/New_York">America/New York</option>' in form
    assert 'value="Etc/GMT+1"' not in form  # fixed offsets ignore summer time
    assert form.index("Europe/Amsterdam") < form.index("Africa/Abidjan")


async def test_settings_preselect_the_group_time_zone(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(f"{GROUP}/settings")).text

    # The profile menu in the top bar has a select of its own; the form is in the main part.
    form = page.split('<main id="content"', 1)[1]
    assert '<option value="Europe/Amsterdam" selected>' in form
    # time zone, dispatch kind, free pick, phone mode, trial call, Webex page (ADR 0075),
    # embedding (the direct call is offered per staffing block since ADR 0072)
    assert form.count(" selected>") == 7


async def test_new_group_rejects_an_unknown_time_zone(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _post(
        site,
        "/admin/groups",
        {
            "organisation": "demo",
            "name": "Mars",
            "slug": "mars",
            "timezone": "Mars/Olympus_Mons",
            "slot_minutes": "30",
        },
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#timezone">Kies een tijdzone uit de lijst.' in response.text  # type: ignore[attr-defined]


async def test_new_group_errors_are_linked_to_their_fields(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        "/admin/groups",
        data={
            "organisation": "demo",
            "name": "Dubbel",
            "slug": "demo",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "30",
        },
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422
    text = response.text
    assert '<a href="#slug">Dit webadres is al in gebruik' in text
    assert 'aria-describedby="slug-hint slug-error"' in text
    assert 'value="Dubbel"' in text  # the admin's input is kept


async def test_an_empty_web_address_is_made_from_the_name(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _post(
        site,
        "/admin/groups",
        {
            "organisation": "demo",
            "name": "FIN BLD Video groep 1",
            "slug": "",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "30",
        },
    )

    assert response.status_code == 303  # type: ignore[attr-defined]
    assert response.headers["location"] == "/admin/groups/fin-bld-video-groep-1?done=created"  # type: ignore[attr-defined]


async def test_a_typed_web_address_is_tidied_to_the_rules(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _post(
        site,
        "/admin/groups",
        {
            "organisation": "demo",
            "name": "Video",
            "slug": "FINBLDVIDEOGROEP1",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "30",
        },
    )

    assert response.status_code == 303  # type: ignore[attr-defined]
    assert response.headers["location"] == "/admin/groups/finbldvideogroep1?done=created"  # type: ignore[attr-defined]


async def test_a_refused_form_shows_the_web_address_that_was_tried(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await _post(
        site,
        "/admin/groups",
        {
            "organisation": "demo",
            "name": "Demo",
            "slug": "",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "30",
        },
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#slug">Dit webadres is al in gebruik' in response.text  # type: ignore[attr-defined]
    assert 'id="slug" name="slug" type="text" value="demo"' in response.text  # type: ignore[attr-defined]


async def test_outsider_cannot_create_a_group_in_another_organisation(site: Site) -> None:
    await site.login_as(OUTSIDER)

    response = await site.client.post(
        "/admin/groups",
        data={
            "organisation": "demo",
            "name": "Kaping",
            "slug": "kaping",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "30",
        },
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 403
    assert (await site.client.get("/admin/groups/kaping")).status_code == 404


SETTINGS_FORM = {
    "name": "Demo-team",
    "timezone": "Europe/Amsterdam",
    "slot_minutes": "30",
    "min_notice_minutes": "60",
    "horizon_days": "30",
    "dispatch_lead_minutes": "5",
    "dispatch_kind": "contact_center",
    "participant_change_lead_minutes": "60",
    "trial_call_enabled": "yes",
    "join_client": "browser",
    "trial_call_minutes": "30",
    "trial_calls_per_booking": "3",
    "planner_change_min_lead_minutes": "60",
    "queue_visible_from_minutes": "15",
    "max_bookings_per_email": "3",
    "queue_free_pick_allowed": "no",
    "phone_mode": "required",
    "video_host_email": "host-demo@example.nl",
    "wxcc_business_address": "openplanner@example.biz",
    "wxcc_channel": "openplanner-video",
    "wxcc_routing_value": "team-42",
    "not_picked_up_minutes": "10",
    "embedding_enabled": "yes",
}
"""A complete, valid settings form for the demo group."""


async def test_settings_are_saved_and_unchanged_saves_say_so(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    form = dict(SETTINGS_FORM)

    saved = await _post(site, f"{GROUP}/settings", form)
    again = await _post(site, f"{GROUP}/settings", form)

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert again.headers["location"] == f"{GROUP}/settings?done=unchanged"  # type: ignore[attr-defined]
    settings_page = (await site.client.get(f"{GROUP}/settings")).text
    assert (
        '<option value="yes" selected>Ja</option>'
        in settings_page.split('id="embedding_enabled"')[1]
    )
    overview = (await site.client.get(GROUP)).text
    assert "Demo-team" in overview
    assert "Gekoppeld · team-42" in overview
    events = await audit_events(site)
    assert any(e[1] == "group.edit" for e in events)


async def test_group_switches_to_the_own_queue(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    page = (await site.client.get(f"{GROUP}/settings")).text
    form = {
        "name": "Demo",
        "timezone": "Europe/Amsterdam",
        "slot_minutes": "30",
        "min_notice_minutes": "60",
        "horizon_days": "60",
        "dispatch_lead_minutes": "5",
        "dispatch_kind": "openplanner_queue",
        "participant_change_lead_minutes": "120",
        "trial_call_enabled": "yes",
        "join_client": "browser",
        "trial_call_minutes": "30",
        "trial_calls_per_booking": "3",
        "queue_visible_from_minutes": "15",
        "max_bookings_per_email": "3",
        "queue_free_pick_allowed": "no",
        "calendar_invite_address": "team-demo@example.nl",
        "phone_mode": "optional",
        "embedding_enabled": "no",
        "planner_change_min_lead_minutes": "60",
        "not_picked_up_minutes": "10",
    }

    saved = await _post(site, f"{GROUP}/settings", form)

    assert 'name="dispatch_kind"' in page
    assert "Eigen wachtrij van OpenPlanner" in page
    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    after = (await site.client.get(f"{GROUP}/settings")).text
    assert '<option value="openplanner_queue" selected' in after
    assert 'name="participant_change_lead_minutes" type="number" value="120"' in after
    overview = (await site.client.get(GROUP)).text
    assert "team-demo@example.nl" in overview
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=5)
    assert "dispatch_kind" in events[0].event.detail


@pytest.mark.parametrize(
    ("kind", "minutes", "error"),
    [
        ("genesys", "60", '<a href="#dispatch_kind">'),
        (
            "openplanner_queue",
            "2000",
            '<a href="#participant_change_lead_minutes">Vul een aantal minuten',
        ),
        (
            "contact_center",
            "3",
            '<a href="#participant_change_lead_minutes">Vul een aantal minuten',
        ),
    ],
)
async def test_dispatch_fields_are_checked(site: Site, kind: str, minutes: str, error: str) -> None:
    await site.login_as(SUPER)
    form = {
        "name": "Demo",
        "timezone": "Europe/Amsterdam",
        "slot_minutes": "30",
        "min_notice_minutes": "60",
        "horizon_days": "60",
        "dispatch_lead_minutes": "5",
        "dispatch_kind": kind,
        "participant_change_lead_minutes": minutes,
        "trial_call_enabled": "yes",
        "join_client": "browser",
        "trial_call_minutes": "30",
        "trial_calls_per_booking": "3",
        "queue_visible_from_minutes": "15",
        "max_bookings_per_email": "3",
        "queue_free_pick_allowed": "no",
        "phone_mode": "optional",
        "embedding_enabled": "no",
        "planner_change_min_lead_minutes": "60",
        "not_picked_up_minutes": "10",
    }

    response = await _post(site, f"{GROUP}/settings", form)

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert error in response.text  # type: ignore[attr-defined]


async def test_invalid_settings_show_the_field_error(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(
        site,
        f"{GROUP}/settings",
        {
            "name": "Demo",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "7",
            "min_notice_minutes": "60",
            "horizon_days": "60",
            "dispatch_lead_minutes": "5",
            "dispatch_kind": "contact_center",
            "participant_change_lead_minutes": "60",
            "trial_call_enabled": "yes",
            "join_client": "browser",
            "trial_call_minutes": "30",
            "trial_calls_per_booking": "3",
            "planner_change_min_lead_minutes": "60",
            "queue_visible_from_minutes": "15",
            "max_bookings_per_email": "3",
            "queue_free_pick_allowed": "no",
            "phone_mode": "optional",
            "not_picked_up_minutes": "10",
            "embedding_enabled": "no",
        },
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#slot_minutes">Vul een duur in van 5 tot 480 minuten' in response.text  # type: ignore[attr-defined]
    assert 'id="slot_minutes" name="slot_minutes" type="number" value="7"' in response.text  # type: ignore[attr-defined]


async def test_group_admin_adds_and_removes_opening_hours(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    added = await _post(
        site,
        f"{GROUP}/staffing",
        {"weekday": "5", "opens": "10:00", "closes": "12:00", "capacity": "3"},
    )
    page = (await site.client.get(f"{GROUP}/staffing?done=added")).text

    assert added.headers["location"] == f"{GROUP}/staffing?done=added"  # type: ignore[attr-defined]
    assert "Toegevoegd." in page
    assert "10:00–12:00" in page
    assert "3 medewerkers" in page
    # Native time and date inputs follow the browser locale (AM/PM, mm/dd/yyyy).
    assert 'type="time"' not in page
    assert 'type="date"' not in page
    assert 'id="valid_from" name="valid_from" type="text" inputmode="numeric"' in page
    assert "dd-mm-jjjj" in page
    assert 'id="opens" name="opens" type="text" inputmode="numeric" value="09:00"' in page
    action = page.split("10:00–12:00", 1)[1].split('action="', 1)[1].split('"', 1)[0]
    removed = await site.client.post(action, headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{GROUP}/staffing?done=removed"
    assert "10:00–12:00" not in (await site.client.get(f"{GROUP}/staffing")).text


async def _saturday_window(site: Site) -> tuple[str, str]:
    """Add Saturday 10:00–12:00 as a group admin; return the week page and its change link."""
    await site.login_as(GROUP_ADMIN)
    await _post(
        site,
        f"{GROUP}/staffing",
        {"weekday": "5", "opens": "10:00", "closes": "12:00", "capacity": "3"},
    )
    week = (await site.client.get(f"{GROUP}/staffing")).text
    return week, week.split("10:00–12:00", 1)[1].split('href="', 1)[1].split('"', 1)[0]


async def test_group_admin_changes_existing_opening_hours(site: Site) -> None:
    week, link = await _saturday_window(site)
    assert 'aria-label="Tijdblok zaterdag 10:00–12:00 wijzigen"' in week

    form = (await site.client.get(link)).text
    assert '<option value="5" selected>' in form
    assert 'id="opens" name="opens" type="text" inputmode="numeric" value="10:00"' in form
    assert 'id="capacity" name="capacity" type="number" value="3"' in form

    saved = await _post(
        site, link, {"weekday": "5", "opens": "09:00", "closes": "12:00", "capacity": "4"}
    )
    assert saved.headers["location"] == f"{GROUP}/staffing?done=saved"  # type: ignore[attr-defined]
    page = (await site.client.get(f"{GROUP}/staffing?done=saved")).text
    assert "09:00–12:00" in page
    assert "4 medewerkers" in page
    assert "10:00–12:00" not in page


async def test_changed_hours_are_validated_like_new_ones(site: Site) -> None:
    _, link = await _saturday_window(site)

    response = await _post(
        site, link, {"weekday": "5", "opens": "10:00", "closes": "12:00", "capacity": "1001"}
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert 'href="#capacity"' in response.text  # type: ignore[attr-defined]
    assert "10:00–12:00" in (await site.client.get(f"{GROUP}/staffing")).text


async def test_a_block_without_agents_gets_a_label(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    form = (await site.client.get(f"{GROUP}/staffing")).text
    assert 'id="capacity" name="capacity" type="number" value="1" required' in form
    assert 'min="0"' in form
    assert 'id="label" name="label" type="text" value=""' in form

    added = await _post(
        site,
        f"{GROUP}/staffing",
        {"weekday": "5", "opens": "12:00", "closes": "12:30", "capacity": "0", "label": "Lunch"},
    )
    week = (await site.client.get(f"{GROUP}/staffing")).text
    link = week.split("12:00–12:30", 1)[1].split('href="', 1)[1].split('"', 1)[0]
    change = (await site.client.get(link)).text

    assert added.status_code == 303  # type: ignore[attr-defined]
    assert 'id="capacity" name="capacity" type="number" value="0"' in change
    assert 'id="label" name="label" type="text" value="Lunch"' in change
    events = [(b, d) for _, b, _, d in await audit_events(site)]
    assert all("Lunch" not in str(detail) for _, detail in events)  # admin content, no audit


async def test_an_invalid_label_is_explained(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _post(
        site,
        f"{GROUP}/staffing",
        {"weekday": "5", "opens": "12:00", "closes": "12:30", "capacity": "0", "label": "x" * 51},
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#label">' in response.text  # type: ignore[attr-defined]
    assert "50 tekens" in response.text  # type: ignore[attr-defined]


async def test_every_day_links_to_the_form_with_that_day_chosen(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    week = (await site.client.get(f"{GROUP}/staffing")).text
    form = (await site.client.get(f"{GROUP}/staffing?weekday=6")).text
    bogus = (await site.client.get(f"{GROUP}/staffing?weekday=9")).text

    assert f'href="{GROUP}/staffing?weekday=5#weekday"' in week
    assert 'aria-label="Tijdblok op zaterdag toevoegen"' in week
    assert '<option value="6" selected>' in form
    assert '<option value="0" selected>' in bogus


async def test_overlapping_hours_are_explained(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _post(
        site,
        f"{GROUP}/staffing",
        {"weekday": "0", "opens": "10:00", "closes": "11:00", "capacity": "1"},
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#opens">Deze tijden overlappen' in response.text  # type: ignore[attr-defined]


async def test_exceptions_close_a_day_or_change_hours(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    day = (site.clock.now() + timedelta(days=3)).strftime("%d-%m-%Y")  # as typed by an admin
    other = (site.clock.now() + timedelta(days=4)).date().isoformat()

    closed = await _post(site, f"{GROUP}/exceptions", {"day": day, "kind": "closed"})
    hours = await _post(
        site,
        f"{GROUP}/exceptions",
        {"day": other, "kind": "hours", "opens": "13:00", "closes": "14:00", "capacity": "1"},
    )
    page = (await site.client.get(f"{GROUP}/exceptions")).text

    assert closed.headers["location"] == f"{GROUP}/exceptions?done=added"  # type: ignore[attr-defined]
    assert hours.headers["location"] == f"{GROUP}/exceptions?done=added"  # type: ignore[attr-defined]
    assert "Geen bezetting" in page
    assert "13:00–14:00" in page


async def test_an_exception_may_have_no_agents_and_a_label(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    day = (site.clock.now() + timedelta(days=3)).date().isoformat()
    form = (await site.client.get(f"{GROUP}/exceptions")).text
    assert 'id="label" name="label" type="text"' in form

    response = await _post(
        site,
        f"{GROUP}/exceptions",
        {
            "day": day,
            "kind": "hours",
            "opens": "09:00",
            "closes": "12:00",
            "capacity": "0",
            "label": "Heidag",
        },
    )

    assert response.headers["location"] == f"{GROUP}/exceptions?done=added"  # type: ignore[attr-defined]
    assert "09:00–12:00" in (await site.client.get(f"{GROUP}/exceptions")).text


async def test_exception_in_the_past_is_refused(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    yesterday = (site.clock.now() - timedelta(days=1)).date().isoformat()

    response = await _post(site, f"{GROUP}/exceptions", {"day": yesterday, "kind": "closed"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#day">Kies een datum vanaf vandaag' in response.text  # type: ignore[attr-defined]


async def test_changes_need_a_recent_login(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    site.clock.advance(timedelta(minutes=61))

    form = await site.client.get(f"{GROUP}/staffing")
    post = await site.client.post(
        f"{GROUP}/staffing",
        data={"weekday": "5", "opens": "10:00", "closes": "11:00", "capacity": "1"},
        headers={**SAME_ORIGIN, "referer": f"http://testserver{GROUP}/staffing"},
    )
    overview = await site.client.get(GROUP)  # reading needs no fresh login

    assert form.status_code == 303
    assert form.headers["location"] == f"/admin/login?next={GROUP}/staffing"
    assert post.status_code == 303
    assert post.headers["location"] == f"/admin/login?next={GROUP}/staffing"
    assert overview.status_code == 200


async def test_forged_referer_cannot_leave_the_portal(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    site.clock.advance(timedelta(minutes=61))

    post = await site.client.post(
        f"{GROUP}/staffing",
        data={},
        headers={**SAME_ORIGIN, "referer": "https://evil.example/steal"},
    )

    assert post.headers["location"] == "/admin/login?next=/admin/"


async def test_cross_site_changes_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        f"{GROUP}/staffing",
        data={"weekday": "5", "opens": "10:00", "closes": "11:00", "capacity": "1"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
    assert "10:00–11:00" not in (await site.client.get(f"{GROUP}/staffing")).text


async def test_tabs_and_actions_follow_the_matrix(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    group_admin = (await site.client.get(GROUP)).text
    dashboard = (await site.client.get("/admin/")).text
    await site.login_as(ORG_ADMIN)
    org_admin = (await site.client.get(GROUP)).text

    assert f'href="{GROUP}/staffing"' in group_admin
    assert f'href="{GROUP}/settings"' not in group_admin
    assert 'href="/admin/groups/new"' not in dashboard
    assert f'href="{GROUP}/settings"' in org_admin


async def test_names_are_escaped(site: Site) -> None:
    await site.login_as(SUPER)
    await site.client.post(
        "/admin/groups",
        data={
            "organisation": "demo",
            "name": "<script>alert(1)</script>",
            "slug": "xss-test",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "30",
        },
        headers=SAME_ORIGIN,
    )

    text = (await site.client.get("/admin/groups/xss-test")).text

    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


async def test_overview_shows_week_preview_and_exceptions(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(GROUP)).text

    assert "Bezetting per week" in text
    assert "Zo zien deelnemers het" in text
    assert "tijdsloten" in text


async def test_audit_log_says_what_changed(site: Site) -> None:
    await site.login_as(SUPER)
    await _post(
        site,
        f"{GROUP}/staffing",
        {"weekday": "5", "opens": "10:00", "closes": "11:00", "capacity": "1"},
    )
    await _post(
        site,
        f"{GROUP}/settings",
        {
            "name": "Demo-team",
            "timezone": "Europe/Amsterdam",
            "slot_minutes": "30",
            "min_notice_minutes": "60",
            "horizon_days": "45",
            "dispatch_lead_minutes": "5",
            "dispatch_kind": "contact_center",
            "participant_change_lead_minutes": "60",
            "trial_call_enabled": "yes",
            "join_client": "browser",
            "trial_call_minutes": "30",
            "trial_calls_per_booking": "3",
            "planner_change_min_lead_minutes": "60",
            "queue_visible_from_minutes": "15",
            "max_bookings_per_email": "3",
            "queue_free_pick_allowed": "no",
            "phone_mode": "optional",
            "not_picked_up_minutes": "10",
            "embedding_enabled": "no",
        },
    )

    text = (await site.client.get("/admin/audit")).text

    assert "Tijdblok toegevoegd" in text
    assert "Vooruit boeken (dagen), Naam" in text


async def test_oversized_forms_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        f"{GROUP}/staffing", data={"weekday": "5", "padding": "x" * 20_000}, headers=SAME_ORIGIN
    )

    assert response.status_code == 413


async def test_the_direct_call_cap_is_optional(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    form = {
        "name": "Demo",
        "timezone": "Europe/Amsterdam",
        "slot_minutes": "30",
        "min_notice_minutes": "60",
        "horizon_days": "60",
        "dispatch_lead_minutes": "5",
        "dispatch_kind": "contact_center",
        "participant_change_lead_minutes": "60",
        "trial_call_enabled": "yes",
        "join_client": "browser",
        "trial_call_minutes": "30",
        "trial_calls_per_booking": "3",
        "planner_change_min_lead_minutes": "60",
        "queue_visible_from_minutes": "15",
        "max_bookings_per_email": "3",
        "queue_free_pick_allowed": "no",
        "phone_mode": "optional",
        "not_picked_up_minutes": "10",
        "embedding_enabled": "no",
    }
    field = 'id="direct_call_max_concurrent" name="direct_call_max_concurrent" type="number"'

    capped = await _post(site, f"{GROUP}/settings", {**form, "direct_call_max_concurrent": "4"})
    page = (await site.client.get(f"{GROUP}/settings")).text
    zero = await _post(site, f"{GROUP}/settings", {**form, "direct_call_max_concurrent": "0"})
    text = await _post(site, f"{GROUP}/settings", {**form, "direct_call_max_concurrent": "veel"})
    cleared = await _post(site, f"{GROUP}/settings", {**form, "direct_call_max_concurrent": ""})

    assert capped.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert f'{field} value="4"' in page
    for refused in (zero, text):
        assert refused.status_code == 422  # type: ignore[attr-defined]
        assert "Vul een geheel getal van 1 tot en met 1000 in" in refused.text  # type: ignore[attr-defined]
    assert cleared.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert f'{field} value=""' in (await site.client.get(f"{GROUP}/settings")).text


async def test_old_hours_paths_redirect_permanently_to_staffing(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    week = await site.client.get("/admin/groups/demo/hours?weekday=5")
    change = await site.client.get("/admin/groups/demo/hours/7")

    assert week.status_code == 301
    assert week.headers["location"] == f"{GROUP}/staffing?weekday=5"
    assert change.status_code == 301
    assert change.headers["location"] == f"{GROUP}/staffing/7"


async def test_week_shows_labels_and_marks_blocks_without_agents_in_text(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    for opens, closes, capacity, label in (
        ("09:00", "12:00", "1", ""),
        ("12:00", "12:30", "0", "Lunch"),
        ("12:30", "17:00", "3", ""),
    ):
        await _post(
            site,
            f"{GROUP}/staffing",
            {
                "weekday": "5",
                "opens": opens,
                "closes": closes,
                "capacity": capacity,
                "label": label,
            },
        )

    week = (await site.client.get(f"{GROUP}/staffing")).text
    lunch = week.split("12:00–12:30", 1)[1].split("</li>", 1)[0]

    assert "1 medewerker<" in week
    assert "3 medewerkers" in week
    assert "geen medewerkers" in lunch
    assert "Lunch" in lunch
    assert 'class="window window--unstaffed"' in week
    assert 'aria-label="Tijdblok zaterdag 12:00–12:30 splitsen"' in lunch
    assert 'aria-label="Tijdblok zaterdag 12:00–12:30 verwijderen"' in lunch
    overview = (await site.client.get(GROUP)).text
    assert "geen medewerkers" in overview
    assert "splitsen" not in overview  # the overview only shows the week


async def test_group_admin_splits_a_block_for_lunch(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await _post(
        site,
        f"{GROUP}/staffing",
        {"weekday": "5", "opens": "09:00", "closes": "17:00", "capacity": "3", "label": "Dag"},
    )
    week = (await site.client.get(f"{GROUP}/staffing")).text
    link = week.split("09:00–17:00", 1)[1].split('href="', 2)[2].split('"', 1)[0]
    assert link.endswith("/split")

    form = (await site.client.get(link)).text
    assert "Tijdblok splitsen" in form
    assert "zaterdag 09:00–17:00 · 3 medewerkers · Dag" in form
    assert 'id="split_at" name="split_at"' in form
    split = await _post(site, link, {"split_at": "12:00", "capacity": "0", "label": "Lunch"})

    assert split.headers["location"] == f"{GROUP}/staffing?done=saved"  # type: ignore[attr-defined]
    page = (await site.client.get(f"{GROUP}/staffing")).text
    assert "09:00–12:00" in page
    assert "12:00–17:00" in page
    assert "09:00–17:00" not in page
    await site.login_as(SUPER)
    audit = (await site.client.get("/admin/audit")).text
    assert "Tijdblok gesplitst" in audit
    assert "Lunch" not in audit  # admin content, never in the audit log


async def test_a_split_outside_the_block_is_explained(site: Site) -> None:
    week, _ = await _saturday_window(site)
    link = week.split("10:00–12:00", 1)[1].split('href="', 2)[2].split('"', 1)[0]

    response = await _post(site, link, {"split_at": "13:00", "capacity": "1", "label": ""})
    garbage = await _post(site, link, {"split_at": "later", "capacity": "-1", "label": ""})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#split_at">' in response.text  # type: ignore[attr-defined]
    assert "vóór het einde van het tijdblok" in response.text  # type: ignore[attr-defined]
    assert garbage.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#split_at">' in garbage.text  # type: ignore[attr-defined]
    assert "10:00–12:00" in (await site.client.get(f"{GROUP}/staffing")).text


@pytest.mark.parametrize(
    ("field", "value"),
    [("trial_call_minutes", "9"), ("trial_call_minutes", "61"), ("trial_calls_per_booking", "0")],
)
async def test_trial_call_fields_are_checked(site: Site, field: str, value: str) -> None:
    await site.login_as(SUPER)
    form = {**SETTINGS_FORM, field: value}

    response = await _post(site, f"{GROUP}/settings", form)

    assert response.status_code == 422
    assert f'<a href="#{field}">' in response.text


async def test_the_trial_call_switch_and_limits_are_saved(site: Site) -> None:
    await site.login_as(SUPER)
    form = {
        **SETTINGS_FORM,
        "trial_call_enabled": "no",
        "trial_call_minutes": "45",
        "trial_calls_per_booking": "5",
    }

    saved = await _post(site, f"{GROUP}/settings", form)
    after = (await site.client.get(f"{GROUP}/settings")).text

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert 'name="trial_call_minutes" type="number" value="45"' in after
    assert 'name="trial_calls_per_booking" type="number" value="5"' in after
    assert '<option value="no" selected' in after


async def test_the_join_client_is_saved_and_shown(site: Site) -> None:
    await site.login_as(SUPER)

    default = (await site.client.get(f"{GROUP}/settings")).text
    saved = await _post(site, f"{GROUP}/settings", {**SETTINGS_FORM, "join_client": "choice"})
    after = (await site.client.get(f"{GROUP}/settings")).text

    assert '<option value="browser" selected' in default
    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert '<option value="choice" selected' in after


async def test_an_unknown_join_client_is_refused_and_nothing_changes(site: Site) -> None:
    await site.login_as(SUPER)

    refused = await _post(site, f"{GROUP}/settings", {**SETTINGS_FORM, "join_client": "app"})
    after = (await site.client.get(f"{GROUP}/settings")).text

    assert refused.status_code == 422
    assert '<option value="browser" selected' in after


# --- archiving (ADR 0084) --------------------------------------------------------------------


async def test_the_settings_offer_archiving_to_those_who_may(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    settings = (await site.client.get(f"{GROUP}/settings")).text
    assert f"{GROUP}/settings/archive" in settings

    page = await site.client.get(f"{GROUP}/settings/archive")
    assert page.status_code == 200
    assert 'name="confirmation"' in page.text
    assert "Typ “Demo: videogesprek” over om te bevestigen." in page.text

    await site.login_as(GROUP_ADMIN)
    assert f"{GROUP}/settings/archive" not in (await site.client.get(GROUP)).text
    assert (await site.client.get(f"{GROUP}/settings/archive")).status_code == 403


async def test_a_wrong_name_keeps_the_group(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await _post(site, f"{GROUP}/settings/archive", {"confirmation": "Demo-team"})

    assert refused.status_code == 422  # type: ignore[attr-defined]
    assert "Typ de naam precies zo over" in refused.text  # type: ignore[attr-defined]
    assert 'aria-invalid="true"' in refused.text  # type: ignore[attr-defined]
    assert (await site.client.get("/g/demo")).status_code == 200


async def test_archiving_removes_the_public_page_and_makes_the_group_read_only(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    done = await _post(site, f"{GROUP}/settings/archive", {"confirmation": " demo: VIDEOGESPREK "})

    assert done.headers["location"] == "/admin/groups?done=archived"  # type: ignore[attr-defined]
    listing = (await site.client.get("/admin/groups?done=archived")).text
    assert "De groep is gearchiveerd" in listing
    assert f'href="{GROUP}"' not in listing
    archived = (await site.client.get("/admin/groups?archived=1")).text
    assert f'href="{GROUP}"' in archived
    assert "Gearchiveerde groepen" in archived

    assert (await site.client.get("/g/demo")).status_code == 404
    overview = await site.client.get(GROUP)
    assert overview.status_code == 200
    assert "Deze groep is gearchiveerd" in overview.text
    refused = await _post(site, f"{GROUP}/settings", dict(SETTINGS_FORM))
    assert refused.status_code == 409  # type: ignore[attr-defined]
    assert "gearchiveerd en kan niet meer worden gewijzigd" in refused.text  # type: ignore[attr-defined]
    again = await _post(site, f"{GROUP}/settings/archive", {"confirmation": "Demo: videogesprek"})
    assert again.status_code == 409  # type: ignore[attr-defined]

    assert ("sub-org", "group.delete", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]
    assert "gearchiveerd" in (await site.client.get("/admin/audit?group=demo")).text


async def test_archiving_is_refused_while_bookings_lie_ahead(site: Site) -> None:
    booked = await site.client.post(
        "/g/demo/book",
        data={
            "start": "2026-01-02T08:00:00Z",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.org",
            "phone": "",
        },
        headers=PARTICIPANT,
    )
    assert booked.status_code == 303
    await confirm_newest(site.client)
    await site.login_as(SUPER)

    page = (await site.client.get(f"{GROUP}/settings/archive")).text
    refused = await _post(site, f"{GROUP}/settings/archive", {"confirmation": "Demo: videogesprek"})

    assert "afspraken in de toekomst" in page
    assert refused.status_code == 422  # type: ignore[attr-defined]
    assert (await site.client.get("/g/demo")).status_code == 200


# --- changing an exception in place (ADR 0084 step 5) -----------------------------------------


async def test_an_exception_is_changed_from_its_own_page(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    day = (site.clock.now() + timedelta(days=3)).date().isoformat()
    await _post(site, f"{GROUP}/exceptions", {"day": day, "kind": "closed"})
    listing = (await site.client.get(f"{GROUP}/exceptions")).text
    link = re.search(rf'href="({GROUP}/exceptions/\d+)"', listing)
    assert link, "the list links each exception to its change page"

    form = (await site.client.get(link.group(1))).text
    assert 'id="kind-closed" name="kind" value="closed" checked' in form
    changed = await _post(
        site,
        link.group(1),
        {"day": day, "kind": "hours", "opens": "13:00", "closes": "14:00", "capacity": "2"},
    )
    again = await _post(
        site,
        link.group(1),
        {"day": day, "kind": "hours", "opens": "13:00", "closes": "14:00", "capacity": "2"},
    )

    assert changed.headers["location"] == f"{GROUP}/exceptions?done=saved"  # type: ignore[attr-defined]
    assert again.headers["location"] == f"{GROUP}/exceptions?done=unchanged"  # type: ignore[attr-defined]
    assert "13:00–14:00" in (await site.client.get(f"{GROUP}/exceptions")).text
    assert "13:00" in (await site.client.get(link.group(1))).text  # pre-filled with the new hours
    assert any(e[1] == "schedule.manage" for e in await audit_events(site))


async def test_a_changed_exception_in_the_past_or_of_another_group_is_refused(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    day = (site.clock.now() + timedelta(days=3)).date().isoformat()
    yesterday = (site.clock.now() - timedelta(days=1)).date().isoformat()
    await _post(site, f"{GROUP}/exceptions", {"day": day, "kind": "closed"})
    link = re.search(
        rf'href="({GROUP}/exceptions/\d+)"', (await site.client.get(f"{GROUP}/exceptions")).text
    )
    assert link

    past = await _post(site, link.group(1), {"day": yesterday, "kind": "closed"})
    elsewhere = await site.client.get("/admin/groups/andere-team/exceptions/1")

    assert past.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#day">Kies een datum vanaf vandaag' in past.text  # type: ignore[attr-defined]
    assert elsewhere.status_code == 403
