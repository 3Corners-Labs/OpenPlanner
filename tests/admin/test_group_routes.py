"""Group and planning screens (WP7b): forms, confirmations, errors and fresh login."""

from datetime import timedelta

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events

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

    form = (await site.client.get(f"{GROUP}/settings")).text

    assert '<option value="Europe/Amsterdam" selected>' in form
    assert form.count(" selected>") == 3  # time zone, phone mode, direct call


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


async def test_settings_are_saved_and_unchanged_saves_say_so(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    form = {
        "name": "Demo-team",
        "timezone": "Europe/Amsterdam",
        "slot_minutes": "30",
        "min_notice_minutes": "60",
        "horizon_days": "30",
        "dispatch_lead_minutes": "5",
        "phone_mode": "required",
        "video_host_email": "host-demo@example.nl",
        "wxcc_business_address": "openplanner@example.biz",
        "wxcc_channel": "openplanner-video",
        "wxcc_routing_value": "team-42",
        "direct_call_enabled": "yes",
        "not_picked_up_minutes": "10",
    }

    saved = await _post(site, f"{GROUP}/settings", form)
    again = await _post(site, f"{GROUP}/settings", form)

    assert saved.headers["location"] == f"{GROUP}/settings?done=saved"  # type: ignore[attr-defined]
    assert again.headers["location"] == f"{GROUP}/settings?done=unchanged"  # type: ignore[attr-defined]
    overview = (await site.client.get(GROUP)).text
    assert "Demo-team" in overview
    assert "Gekoppeld · team-42" in overview
    events = await audit_events(site)
    assert any(e[1] == "group.edit" for e in events)


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
            "phone_mode": "optional",
            "direct_call_enabled": "no",
            "not_picked_up_minutes": "10",
        },
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#slot_minutes">Vul een duur in van 5 tot 480 minuten' in response.text  # type: ignore[attr-defined]
    assert 'id="slot_minutes" name="slot_minutes" type="number" value="7"' in response.text  # type: ignore[attr-defined]


async def test_group_admin_adds_and_removes_opening_hours(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    added = await _post(
        site,
        f"{GROUP}/hours",
        {"weekday": "5", "opens": "10:00", "closes": "12:00", "capacity": "3"},
    )
    page = (await site.client.get(f"{GROUP}/hours?done=added")).text

    assert added.headers["location"] == f"{GROUP}/hours?done=added"  # type: ignore[attr-defined]
    assert "Toegevoegd." in page
    assert "10:00–12:00" in page
    assert "tot 3 tegelijk" in page
    action = page.split("10:00–12:00", 1)[1].split('action="', 1)[1].split('"', 1)[0]
    removed = await site.client.post(action, headers=SAME_ORIGIN)
    assert removed.headers["location"] == f"{GROUP}/hours?done=removed"
    assert "10:00–12:00" not in (await site.client.get(f"{GROUP}/hours")).text


async def _saturday_window(site: Site) -> tuple[str, str]:
    """Add Saturday 10:00–12:00 as a group admin; return the week page and its change link."""
    await site.login_as(GROUP_ADMIN)
    await _post(
        site,
        f"{GROUP}/hours",
        {"weekday": "5", "opens": "10:00", "closes": "12:00", "capacity": "3"},
    )
    week = (await site.client.get(f"{GROUP}/hours")).text
    return week, week.split("10:00–12:00", 1)[1].split('href="', 1)[1].split('"', 1)[0]


async def test_group_admin_changes_existing_opening_hours(site: Site) -> None:
    week, link = await _saturday_window(site)
    assert 'aria-label="Openingstijd zaterdag 10:00–12:00 wijzigen"' in week

    form = (await site.client.get(link)).text
    assert '<option value="5" selected>' in form
    assert 'id="opens" name="opens" type="time" value="10:00"' in form
    assert 'id="capacity" name="capacity" type="number" value="3"' in form

    saved = await _post(
        site, link, {"weekday": "5", "opens": "09:00", "closes": "12:00", "capacity": "4"}
    )
    assert saved.headers["location"] == f"{GROUP}/hours?done=saved"  # type: ignore[attr-defined]
    page = (await site.client.get(f"{GROUP}/hours?done=saved")).text
    assert "09:00–12:00" in page
    assert "tot 4 tegelijk" in page
    assert "10:00–12:00" not in page


async def test_changed_hours_are_validated_like_new_ones(site: Site) -> None:
    _, link = await _saturday_window(site)

    response = await _post(
        site, link, {"weekday": "5", "opens": "10:00", "closes": "12:00", "capacity": "0"}
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert 'href="#capacity"' in response.text  # type: ignore[attr-defined]
    assert "10:00–12:00" in (await site.client.get(f"{GROUP}/hours")).text


async def test_every_day_links_to_the_form_with_that_day_chosen(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    week = (await site.client.get(f"{GROUP}/hours")).text
    form = (await site.client.get(f"{GROUP}/hours?weekday=6")).text
    bogus = (await site.client.get(f"{GROUP}/hours?weekday=9")).text

    assert f'href="{GROUP}/hours?weekday=5#weekday"' in week
    assert 'aria-label="Openingstijd op zaterdag toevoegen"' in week
    assert '<option value="6" selected>' in form
    assert '<option value="0" selected>' in bogus
    assert "blok" not in week.lower()


async def test_overlapping_hours_are_explained(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _post(
        site,
        f"{GROUP}/hours",
        {"weekday": "0", "opens": "10:00", "closes": "11:00", "capacity": "1"},
    )

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#opens">Deze tijden overlappen' in response.text  # type: ignore[attr-defined]


async def test_exceptions_close_a_day_or_change_hours(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    day = (site.clock.now() + timedelta(days=3)).date().isoformat()
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
    assert "Gesloten" in page
    assert "13:00–14:00" in page


async def test_exception_in_the_past_is_refused(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    yesterday = (site.clock.now() - timedelta(days=1)).date().isoformat()

    response = await _post(site, f"{GROUP}/exceptions", {"day": yesterday, "kind": "closed"})

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert '<a href="#day">Kies een datum vanaf vandaag' in response.text  # type: ignore[attr-defined]


async def test_changes_need_a_recent_login(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    site.clock.advance(timedelta(minutes=61))

    form = await site.client.get(f"{GROUP}/hours")
    post = await site.client.post(
        f"{GROUP}/hours",
        data={"weekday": "5", "opens": "10:00", "closes": "11:00", "capacity": "1"},
        headers={**SAME_ORIGIN, "referer": f"http://testserver{GROUP}/hours"},
    )
    overview = await site.client.get(GROUP)  # reading needs no fresh login

    assert form.status_code == 303
    assert form.headers["location"] == f"/admin/login?next={GROUP}/hours"
    assert post.status_code == 303
    assert post.headers["location"] == f"/admin/login?next={GROUP}/hours"
    assert overview.status_code == 200


async def test_forged_referer_cannot_leave_the_portal(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    site.clock.advance(timedelta(minutes=61))

    post = await site.client.post(
        f"{GROUP}/hours",
        data={},
        headers={**SAME_ORIGIN, "referer": "https://evil.example/steal"},
    )

    assert post.headers["location"] == "/admin/login?next=/admin/"


async def test_cross_site_changes_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        f"{GROUP}/hours",
        data={"weekday": "5", "opens": "10:00", "closes": "11:00", "capacity": "1"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
    assert "10:00–11:00" not in (await site.client.get(f"{GROUP}/hours")).text


async def test_tabs_and_actions_follow_the_matrix(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    group_admin = (await site.client.get(GROUP)).text
    dashboard = (await site.client.get("/admin/")).text
    await site.login_as(ORG_ADMIN)
    org_admin = (await site.client.get(GROUP)).text

    assert f'href="{GROUP}/hours"' in group_admin
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

    assert "Openingstijden per week" in text
    assert "Zo zien deelnemers het" in text
    assert "tijdsloten" in text


async def test_audit_log_says_what_changed(site: Site) -> None:
    await site.login_as(SUPER)
    await _post(
        site,
        f"{GROUP}/hours",
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
            "phone_mode": "optional",
            "direct_call_enabled": "no",
            "not_picked_up_minutes": "10",
        },
    )

    text = (await site.client.get("/admin/audit")).text

    assert "Openingstijd toegevoegd" in text
    assert "Vooruit boeken (dagen), Naam" in text


async def test_oversized_forms_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        f"{GROUP}/hours", data={"weekday": "5", "padding": "x" * 20_000}, headers=SAME_ORIGIN
    )

    assert response.status_code == 413
