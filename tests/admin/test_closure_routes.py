"""Emergency closure screens (ADR 0024): closing, informing citizens and handling bookings.

Calendar: "now" is Thursday 1 January 2026 08:00 UTC; the demo group is open on weekdays
09:00-12:00 and 13:00-17:00 Amsterdam time, two places per slot.
"""

import re

import httpx

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site
from tests.admin.test_admin_flow import audit_events
from tests.support.participant import confirm_newest

CLOSURE = "/admin/groups/demo/closure"
FRIDAY_0900 = "2026-01-02T08:00:00Z"  # 09:00 Amsterdam
MONDAY_0900 = "2026-01-05T08:00:00+00:00"
CITIZEN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
NOTICES = {
    "notice_nl": "Door een stroomstoring zijn wij gesloten.\nWe excuseren ons <b>hiervoor</b>.",
    "notice_en": "We are closed because of a power cut.",
}
UNTIL_SATURDAY = {"ends_at": "2026-01-03", "ends_at_time": "00:00", **NOTICES}


async def _book(site: Site, start: str = FRIDAY_0900) -> str:
    """Book as a citizen and return the management path ``/b/{token}``."""
    response = await site.client.post(
        "/g/demo/book",
        data={
            "start": start,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.org",
            "phone": "",
        },
        headers=CITIZEN,
    )
    assert response.status_code == 303
    return f"/b/{await confirm_newest(site.client)}"


async def _post(site: Site, path: str, data: dict[str, str] | None = None) -> httpx.Response:
    return await site.client.post(path, data=data or {}, headers=SAME_ORIGIN)


async def _close(site: Site) -> str:
    """Book Friday 09:00, close the group until Saturday as the group admin; return the booking."""
    manage = await _book(site)
    await site.login_as(GROUP_ADMIN)
    response = await _post(site, CLOSURE, UNTIL_SATURDAY)
    assert response.status_code == 303
    assert response.headers["location"] == f"{CLOSURE}?done=closure_started&count=1"
    return manage


async def test_group_admin_closes_the_team_and_citizens_see_why(site: Site) -> None:
    manage = await _close(site)

    page = (await site.client.get(f"{CLOSURE}?done=closure_started&count=1")).text
    assert "Opgeschorte afspraken: 1" in page
    assert re.search(r"OP-\d{6}", page)
    assert "ada@example.org" not in page  # the admin list shows no contact details
    assert "We excuseren ons &lt;b&gt;hiervoor&lt;/b&gt;." in page  # escaped, never HTML

    public = (await site.client.get("/g/demo")).text
    assert '<h1 id="closure-heading">Tijdelijk gesloten</h1>' in public  # no heading given
    assert "Door een stroomstoring zijn wij gesloten." in public
    assert "<b>hiervoor</b>" not in public

    booking = (await site.client.get(manage)).text
    assert "Opgeschort" in booking
    assert "Uw afspraak kan niet doorgaan op dit moment" in booking
    assert f'href="{manage}/reschedule"' in booking
    english = (await site.client.get(manage, params={"lang": "en"})).text
    assert "We are closed because of a power cut." in english
    assert ("sub-group", "closure.manage", "success", "") in [
        (a, b, str(c), d) for a, b, c, d in await audit_events(site)
    ]


async def test_closed_team_cannot_be_booked(site: Site) -> None:
    await _close(site)

    response = await site.client.post(
        "/g/demo/book",
        data={
            "start": "2026-01-02T09:00:00Z",
            "first_name": "Bo",
            "last_name": "Berg",
            "email": "bo@example.org",
        },
        headers=CITIZEN,
    )

    assert response.status_code == 409
    assert "tijdelijk gesloten" in response.text


async def test_closing_errors_are_linked_to_their_fields(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _post(site, CLOSURE, {"ends_at": "morgen", "notice_nl": "Dicht"})

    assert response.status_code == 422
    assert '<a href="#ends_at">' in response.text
    assert 'id="ends_at_time" name="ends_at_time" type="text" inputmode="numeric"' in response.text
    response = await _post(site, CLOSURE, {"notice_nl": "Dicht", "notice_en": ""})
    assert response.status_code == 422
    assert '<a href="#notice_en">' in response.text
    assert 'aria-describedby="notice_en-hint notice_en-error"' in response.text


async def test_closing_twice_is_explained(site: Site) -> None:
    await _close(site)
    # The update form replaces the closing form; a stale form posted again is refused.
    response = await _post(site, CLOSURE, UNTIL_SATURDAY)

    assert response.status_code == 422
    assert "Dit team heeft al een noodsluiting" in response.text


async def test_update_mails_and_lift_reinstates(site: Site) -> None:
    manage = await _close(site)

    updated = await _post(site, f"{CLOSURE}/update", {**UNTIL_SATURDAY, "notify": "yes"})
    assert updated.headers["location"] == f"{CLOSURE}?done=closure_updated&count=1"
    lifted = await _post(site, f"{CLOSURE}/lift", {"reinstate": "yes"})
    assert lifted.headers["location"] == f"{CLOSURE}?done=closure_lifted&count=1"

    assert "Bevestigd" in (await site.client.get(manage)).text
    assert "Tijdelijk gesloten" not in (await site.client.get("/g/demo")).text


async def test_admin_moves_a_booking_on_hold(site: Site) -> None:
    await _close(site)
    page = (await site.client.get(CLOSURE)).text
    booking_id = re.search(r"/closure/bookings/(\d+)/move", page)
    assert booking_id
    move = f"{CLOSURE}/bookings/{booking_id.group(1)}/move"

    form = (await site.client.get(move)).text
    assert f'<option value="{MONDAY_0900}">' in form
    response = await _post(site, move, {"start": MONDAY_0900})

    assert response.headers["location"] == f"{CLOSURE}?done=moved&count=1"
    after = (await site.client.get(CLOSURE)).text
    assert "Er zijn geen opgeschorte afspraken." in after
    again = await _post(site, move, {"start": MONDAY_0900})
    assert again.status_code == 409


async def test_cancel_all_bookings_on_hold(site: Site) -> None:
    await _close(site)

    response = await _post(site, f"{CLOSURE}/cancel")

    assert response.headers["location"] == f"{CLOSURE}?done=cancelled&count=1"


async def test_org_admin_closes_every_team_of_the_organisation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    nav = (await site.client.get("/admin/")).text
    assert 'href="/admin/closures"' in nav
    listing = (await site.client.get("/admin/closures")).text
    assert 'href="/admin/organisations/demo/closure"' in listing

    response = await _post(site, "/admin/organisations/demo/closure", UNTIL_SATURDAY)
    assert response.headers["location"] == (
        "/admin/organisations/demo/closure?done=organisation_closed&count=1"
    )
    page = (await site.client.get(response.headers["location"])).text
    assert "Teams gesloten: 1." in page
    assert "Tijdelijk gesloten" in page
    assert "Tijdelijk gesloten" not in (await site.client.get("/g/andere-team")).text

    lifted = await _post(site, "/admin/organisations/demo/closure/lift")
    assert lifted.headers["location"].endswith("done=organisation_lifted&count=1")


async def test_group_admin_sees_the_tab_but_not_the_organisation_page(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    group = (await site.client.get("/admin/groups/demo")).text

    assert f'href="{CLOSURE}"' in group
    assert 'href="/admin/closures"' not in group
