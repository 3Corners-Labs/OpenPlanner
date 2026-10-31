"""Holiday screens (ADR 0025): superuser calendars, organisation choice, group override."""

import re
from html import unescape

import httpx

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.admin.test_admin_flow import audit_events

CALENDAR = "/admin/holidays/nationaal"
GROUP = "/admin/groups/demo/holidays"


async def _post(site: Site, path: str, **data: str) -> httpx.Response:
    return await site.client.post(path, data=data, headers=SAME_ORIGIN)


async def _calendar_id(site: Site) -> str:
    page = (await site.client.get("/admin/organisations/demo/holidays")).text
    found = re.search(r'name="calendar-(\d+)"', page)
    assert found is not None
    return found.group(1)


async def _fill_and_assign(site: Site) -> None:
    """As the superuser: add the Dutch holidays of 2026 and let ``demo`` follow them."""
    await site.login_as(SUPER)
    added = await _post(site, f"{CALENDAR}/national", year="2026")
    assert added.headers["location"] == f"{CALENDAR}?year=2026&done=added"
    calendar_id = await _calendar_id(site)
    chosen = await _post(
        site, "/admin/organisations/demo/holidays", **{f"calendar-{calendar_id}": "on"}
    )
    assert chosen.headers["location"] == "/admin/organisations/demo/holidays?done=saved"


async def test_calendars_are_the_superusers_and_an_organisation_chooses_on_its_tab(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    overview = (await site.client.get("/admin/")).text
    organisation = (await site.client.get("/admin/organisations/demo")).text

    # ADR 0047 §4: the calendars sit under Platform; the organisation picks on its own tab.
    assert 'href="/admin/holidays"' not in overview
    assert (await site.client.get("/admin/holidays")).status_code == 403
    assert 'href="/admin/organisations/demo/holidays"' in organisation

    await site.login_as(SUPER)
    listing = (await site.client.get("/admin/holidays")).text
    assert 'href="/admin/holidays"' in (await site.client.get("/admin/")).text
    assert "Nationale feestdagen" in listing
    assert f'href="{CALENDAR}"' in listing
    assert 'action="/admin/holidays"' in listing
    assert "/admin/organisations/demo/holidays" not in listing


async def test_superuser_generates_the_dutch_holidays_once(site: Site) -> None:
    await site.login_as(SUPER)

    first = await _post(site, f"{CALENDAR}/national", year="2026")
    await _post(site, f"{CALENDAR}/national", year="2026")
    page = (await site.client.get(first.headers["location"])).text

    assert "Koningsdag" in page
    assert "maandag 27 april" in page
    assert page.count("<td>Tweede Paasdag</td>") == 1  # running it twice adds nothing
    assert '<h2 id="days-heading">Datums in 2026</h2>' in page


async def test_holiday_names_follow_the_admin_language(site: Site) -> None:
    await site.login_as(SUPER)
    site.client.cookies.set("lang", "en")

    response = await _post(site, f"{CALENDAR}/national", year="2027")
    page = (await site.client.get(response.headers["location"])).text

    assert "King's Day" in page or "King&#39;s Day" in page


async def test_implausible_year_is_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await _post(site, f"{CALENDAR}/national", year="20026")

    assert response.status_code == 422
    assert "Kies een jaar tussen 2000 en 2100." in response.text


async def test_superuser_adds_and_removes_a_single_date(site: Site) -> None:
    await site.login_as(SUPER)

    added = await _post(site, f"{CALENDAR}/days", holiday_day="2026-04-03", name="Goede Vrijdag")
    twice = await _post(site, f"{CALENDAR}/days", holiday_day="2026-04-03", name="Nogmaals")
    page = (await site.client.get(added.headers["location"])).text
    holiday_id = re.search(r"/days/(\d+)/delete", page)

    assert added.headers["location"] == f"{CALENDAR}?year=2026&done=added"
    assert twice.status_code == 422
    assert 'href="#holiday_day"' in twice.text
    assert "<td>Goede Vrijdag</td>" in page
    assert holiday_id is not None
    removed = await site.client.post(
        f"{CALENDAR}/days/{holiday_id.group(1)}/delete?year=2026", headers=SAME_ORIGIN
    )
    assert removed.headers["location"] == f"{CALENDAR}?year=2026&done=removed"
    assert "<td>Goede Vrijdag</td>" not in (await site.client.get(removed.headers["location"])).text


async def test_superuser_creates_and_deletes_a_calendar(site: Site) -> None:
    await site.login_as(SUPER)

    created = await _post(site, "/admin/holidays", name="Carnaval", slug="carnaval")
    taken = await _post(site, "/admin/holidays", name="Nog eens", slug="carnaval")
    deleted = await site.client.post("/admin/holidays/carnaval/delete", headers=SAME_ORIGIN)

    assert created.headers["location"] == "/admin/holidays/carnaval?done=created"
    assert taken.status_code == 422
    assert deleted.headers["location"] == "/admin/holidays?done=removed"
    assert (await site.client.get("/admin/holidays/carnaval")).status_code == 404
    # Creating and deleting are audited; the refused duplicate wrote nothing.
    assert [(a, b, str(c)) for a, b, c, _ in await audit_events(site)] == [
        ("sub-super", "admin.login", "success"),
        ("sub-super", "staff.created", "success"),  # ADR 0046: the first login
        ("sub-super", "holidays.manage", "success"),
        ("sub-super", "holidays.manage", "success"),
    ]


async def test_a_calendar_in_use_cannot_be_deleted(site: Site) -> None:
    await _fill_and_assign(site)

    response = await site.client.post(f"{CALENDAR}/delete", headers=SAME_ORIGIN)

    assert response.status_code == 409
    assert "nog gekozen door een organisatie of groep" in response.text


async def test_an_inherited_holiday_closes_the_participant_calendar(site: Site) -> None:
    before = (await site.client.get("/g/demo")).text
    await _fill_and_assign(site)
    after = (await site.client.get("/g/demo")).text

    # Thursday 1 January 2026 is Nieuwjaarsdag; Friday 2 January stays open.
    assert "Eerst mogelijk:</strong> donderdag 1 januari" in before
    assert "Eerst mogelijk:</strong> vrijdag 2 januari" in after
    assert 'href="/g/demo?day=2026-01-02#times"' in after


async def test_group_admin_keeps_a_holiday_open_and_closes_it_again(site: Site) -> None:
    await _fill_and_assign(site)
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get(GROUP)).text
    opened = await site.client.post(f"{GROUP}/2026-01-01/open", headers=SAME_ORIGIN)
    participant = (await site.client.get("/g/demo")).text
    after = (await site.client.get(GROUP)).text
    closed = await site.client.post(f"{GROUP}/2026-01-01/close", headers=SAME_ORIGIN)

    assert "Nieuwjaarsdag" in page
    assert "Nu: Nationale feestdagen" in page  # what the group inherits
    assert opened.headers["location"] == f"{GROUP}?done=saved"
    assert "Eerst mogelijk:</strong> donderdag 1 januari" in participant
    assert "Gewoon open" in after
    assert closed.status_code == 303
    assert "Eerst mogelijk:</strong> vrijdag 2 januari" in (await site.client.get("/g/demo")).text


async def test_group_admin_chooses_no_holidays_for_their_group(site: Site) -> None:
    await _fill_and_assign(site)
    await site.login_as(GROUP_ADMIN)

    saved = await _post(site, GROUP, source="own")
    page = (await site.client.get(GROUP)).text

    assert saved.headers["location"] == f"{GROUP}?done=saved"
    assert 'value="own" checked' in page
    assert "Er gelden geen feestdagen voor deze groep" in page
    assert "Eerst mogelijk:</strong> donderdag 1 januari" in (await site.client.get("/g/demo")).text


async def test_unknown_source_and_calendar_are_refused(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    no_source = await _post(site, GROUP)
    unknown = await _post(site, GROUP, source="own", **{"calendar-999999": "on"})

    assert no_source.status_code == 422
    assert 'href="#source"' in no_source.text
    assert unknown.status_code == 422
    assert "Kies alleen bestaande kalenders." in unknown.text


async def test_a_malformed_day_is_not_found(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.post(f"{GROUP}/31-12-2026/open", headers=SAME_ORIGIN)

    assert response.status_code == 404


async def test_cross_site_holiday_changes_are_refused(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post(
        f"{CALENDAR}/national", data={"year": "2026"}, headers={"sec-fetch-site": "cross-site"}
    )

    assert response.status_code == 403
    assert "<td>Koningsdag</td>" not in (await site.client.get(CALENDAR)).text


_LABELLED_BUTTON = re.compile(r'<button[^>]*aria-label="([^"]+)"[^>]*>([^<]+)</button>')


async def test_button_labels_contain_their_visible_text(site: Site) -> None:
    # WCAG 2.5.3 (label in name): speech users say what they see ("Toch open").
    await _fill_and_assign(site)
    await site.client.post(f"{GROUP}/2026-01-01/open", headers=SAME_ORIGIN)
    pages = [(await site.client.get(path)).text for path in (GROUP, f"{CALENDAR}?year=2026")]

    buttons = [match for page in pages for match in _LABELLED_BUTTON.findall(page)]
    assert {visible.strip() for _, visible in buttons} >= {
        "Toch open",
        "Weer sluiten",
        "Verwijderen",
    }
    for label, visible in buttons:
        assert unescape(visible).strip().lower() in unescape(label).lower(), (label, visible)


async def test_superuser_renames_a_calendar_and_its_address_stays(site: Site) -> None:
    """ADR 0084 step 5: the page has a rename form; a blank name is explained."""
    await site.login_as(SUPER)
    assert 'name="calendar_name"' in (await site.client.get(CALENDAR)).text

    blank = await site.client.post(CALENDAR, data={"calendar_name": "  "}, headers=SAME_ORIGIN)
    renamed = await site.client.post(
        CALENDAR, data={"calendar_name": "Landelijke feestdagen"}, headers=SAME_ORIGIN
    )
    again = await site.client.post(
        CALENDAR, data={"calendar_name": "Landelijke feestdagen"}, headers=SAME_ORIGIN
    )

    assert blank.status_code == 422
    assert 'href="#calendar_name"' in blank.text
    assert renamed.headers["location"] == f"{CALENDAR}?done=saved"
    assert again.headers["location"] == f"{CALENDAR}?done=unchanged"
    assert "Landelijke feestdagen" in (await site.client.get("/admin/holidays")).text
    assert (await site.client.get(CALENDAR)).status_code == 200
    assert (
        await site.client.post("/admin/holidays/nope", data={}, headers=SAME_ORIGIN)
    ).status_code == 404
