"""Display links of the wallboard: one-time, short-lived, revocable, address-bound (ADR 0117).

The abuse cases of R10 live here: a revoked, expired or used link is refused; the cookie is
refused from another network and another address, on a poll as well as at the start; the token is
in no log, no redirect and no audit event; the cookie means nothing to an admin route.
"""

import re
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.domain.authorization import Scope
from tests.support.admin_site import (
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.support.admin_site import site_uow as _uow
from tests.support.routes import all_routes

ORG = "/admin/organisations/demo"
BOARD_PAGE = f"{ORG}/usage?view=wallboard"
CREATE = f"{ORG}/usage/wallboard/links"
SETTING = f"{ORG}/settings"
TOKEN = re.compile(r"/display/start/([A-Za-z0-9_-]+)")


async def _set(site: Site, key: str, value: str) -> None:
    response = await site.client.post(
        f"{SETTING}/{key}", data={"value": value}, headers=SAME_ORIGIN
    )
    assert response.status_code == 303, key
    app: Any = site.app
    await app.state.live_settings.refresh()


async def _switch_on(site: Site) -> None:
    await _set(site, "reporting_wallboard_display_links_enabled", "true")


def _screen(site: Site, address: str = "10.20.4.31") -> httpx.AsyncClient:
    """A browser on a screen: no admin session, its own cookie jar, its own address."""
    app: Any = site.app
    transport = httpx.ASGITransport(app=app, client=(address, 4711))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _make(site: Site, **form: str) -> str:
    """Make a link as the organisation admin and return its token."""
    fields = {"name": "TV teamruimte", "days": "7", "group": "", "networks": "", **form}
    response = await site.client.post(CREATE, data=fields, headers=SAME_ORIGIN)
    assert response.status_code == 200, response.text[:300]
    found = TOKEN.search(response.text)
    assert found is not None
    return found.group(1)


async def _events(site: Site) -> list[Any]:
    async with _uow(site).read() as tx:
        return list(await tx.list_audit_events(Scope(everything=True), limit=50))


async def test_display_links_are_off_by_default(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(BOARD_PAGE)).text
    refused = await site.client.post(CREATE, data={"name": "TV", "days": "7"}, headers=SAME_ORIGIN)

    assert "Displaylinks staan uit voor deze organisatie." in page
    assert f'action="{CREATE}"' not in page
    assert refused.status_code == 303
    assert "refused=enabled" in refused.headers["location"]


async def test_only_the_organisation_admin_makes_links(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    for who in (GROUP_ADMIN, AUDITOR, OUTSIDER):
        await site.login_as(who)
        response = await site.client.post(
            CREATE, data={"name": "TV", "days": "7"}, headers=SAME_ORIGIN
        )
        assert response.status_code == 403, who.subject
    await site.login_as(GROUP_ADMIN)
    assert (
        'id="links"' not in (await site.client.get("/admin/groups/demo/usage?view=wallboard")).text
    )
    await site.login_as(SUPER)
    assert (
        await site.client.post(CREATE, data={"name": "TV", "days": "7"}, headers=SAME_ORIGIN)
    ).status_code == 200


async def test_the_link_is_shown_once_and_nowhere_else(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    response = await site.client.post(
        CREATE, data={"name": "TV teamruimte", "days": "7"}, headers=SAME_ORIGIN
    )
    token = TOKEN.search(response.text)
    assert token is not None
    page = (await site.client.get(BOARD_PAGE)).text
    events = " ".join(f"{e.event.detail} {e.event.action}" for e in await _events(site))

    assert "Nieuwe displaylink: kopieer hem nu" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert token.group(1) not in page  # the list never shows it again
    assert token.group(1) not in events
    assert "TV teamruimte" in page
    assert "Nog niet geopend" in page


async def test_the_first_request_sets_a_screen_cookie_and_leaves_no_token_in_the_url(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site)

    async with _screen(site) as screen:
        first = await screen.get(f"/display/start/{token}")
        board = await screen.get("/display")
        poll = await screen.get("/display/live")

    assert first.status_code == 303
    assert first.headers["location"] == "/display"
    assert token not in first.headers["location"]
    assert first.headers["referrer-policy"] == "no-referrer"
    cookie = first.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/display" in cookie
    assert token not in cookie  # the cookie is a secret of its own
    assert board.status_code == 200
    assert 'class="wb__tile"' in board.text
    assert "admin-nav" not in board.text
    assert token not in board.text
    assert 'data-poll="/display/live"' in board.text
    assert poll.status_code == 200
    assert "<html" not in poll.text


async def test_a_link_works_once_even_if_it_leaks_later(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site)

    async with _screen(site) as first, _screen(site, "10.20.4.99") as thief:
        assert (await first.get(f"/display/start/{token}")).status_code == 303
        stolen = await thief.get(f"/display/start/{token}")
        still = await first.get("/display/live")

    assert stolen.status_code == 403
    assert "Deze link kan niet meer worden gebruikt." in stolen.text
    assert still.status_code == 200  # the screen that opened it first keeps working


async def test_an_unknown_link_and_a_used_one_read_the_same(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site)

    async with _screen(site) as screen:
        await screen.get(f"/display/start/{token}")
        used = await screen.get(f"/display/start/{token}")
        guess = await screen.get("/display/start/" + "A" * 43)
        long = await screen.get("/display/start/" + "A" * 300)

    assert used.status_code == guess.status_code == long.status_code == 403
    assert used.text == guess.text == long.text


async def test_no_cookie_or_a_wrong_one_shows_nothing(site: Site) -> None:
    async with _screen(site) as screen:
        bare = await screen.get("/display")
        poll = await screen.get("/display/live")
        screen.cookies.set("op_display", "x" * 43, path="/display")
        forged = await screen.get("/display")

    assert bare.status_code == 403
    assert poll.status_code == 403
    assert forged.status_code == 403
    assert "wb__tile" not in forged.text


async def test_a_revoked_link_stops_the_screen_at_its_next_poll(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site)
    async with _screen(site) as screen:
        await screen.get(f"/display/start/{token}")
        assert (await screen.get("/display/live")).status_code == 200
        page = (await site.client.get(BOARD_PAGE)).text
        link_id = re.search(r"wallboard/links/(\d+)/revoke", page)
        assert link_id is not None

        revoked = await site.client.post(f"{CREATE}/{link_id.group(1)}/revoke", headers=SAME_ORIGIN)
        poll = await screen.get("/display/live")
        board = await screen.get("/display")

    assert revoked.status_code == 303
    assert poll.status_code == 403
    assert board.status_code == 403
    after = (await site.client.get(BOARD_PAGE)).text
    assert "Ingetrokken" in after
    assert "wallboard/links/" not in after.split("Ingetrokken", 1)[1].split("</tr>", 1)[0]
    actions = [e.event.detail for e in await _events(site)]
    assert f"revoke:{link_id.group(1)}" in actions
    assert f"create:{link_id.group(1)}" in actions


async def test_the_session_ends_with_the_link(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site, days="1")
    async with _screen(site) as screen:
        await screen.get(f"/display/start/{token}")
        assert (await screen.get("/display/live")).status_code == 200

        site.clock.advance(timedelta(days=1, minutes=1))

        assert (await screen.get("/display/live")).status_code == 403
        assert (await screen.get("/display")).status_code == 403
    await site.login_as(ORG_ADMIN)  # the admin's own session was a day old too
    page = (await site.client.get(BOARD_PAGE)).text
    assert "Verlopen" in page


async def test_a_link_cannot_live_longer_than_the_platform_allows(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    response = await site.client.post(
        CREATE, data={"name": "TV", "days": "31"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert "refused=days" in response.headers["location"]


async def test_the_network_list_applies_at_the_start_and_the_link_stays_valid(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    await _set(site, "reporting_wallboard_link_networks", "10.20.0.0/16")
    token = await _make(site)

    async with _screen(site, "192.0.2.7") as wrong, _screen(site, "10.20.9.9") as right:
        refused = await wrong.get(f"/display/start/{token}")
        again = await wrong.get(f"/display/start/{token}")
        ok = await right.get(f"/display/start/{token}")

    assert refused.status_code == again.status_code == 403
    assert "niet toegestaan vanaf dit netwerk" in refused.text
    assert ok.status_code == 303  # the link was not used up by the refusal
    refusals = [e for e in await _events(site) if e.event.action == "wallboard.session_refused"]
    assert len(refusals) == 1  # one deduplicated event, however often it asks
    assert "192.0.2.7" not in " ".join(e.event.detail + e.event.reason for e in refusals)
    assert refusals[0].event.reason == "network"


async def test_a_link_may_only_narrow_the_default_networks(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    await _set(site, "reporting_wallboard_link_networks", "10.20.0.0/16")

    wider = await site.client.post(
        CREATE, data={"name": "TV", "days": "7", "networks": "10.0.0.0/8"}, headers=SAME_ORIGIN
    )
    narrower = await site.client.post(
        CREATE, data={"name": "TV", "days": "7", "networks": "10.20.4.0/24"}, headers=SAME_ORIGIN
    )
    default = await site.client.post(
        CREATE, data={"name": "TV2", "days": "7", "networks": ""}, headers=SAME_ORIGIN
    )

    assert wider.status_code == 303
    assert "refused=networks" in wider.headers["location"]
    assert narrower.status_code == 200
    assert default.status_code == 200
    page = (await site.client.get(BOARD_PAGE)).text
    assert "10.20.4.0/24" in page
    assert "10.20.0.0/16" in page  # a link without its own list takes the default


async def test_a_pinned_session_works_only_from_the_first_address_on_every_request(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site, pin="on")

    async with _screen(site, "10.20.4.31") as screen:
        assert (await screen.get(f"/display/start/{token}")).status_code == 303
        cookie = screen.cookies.get("op_display", path="/display")
        assert cookie is not None
        assert (await screen.get("/display/live")).status_code == 200
    async with _screen(site, "10.20.4.32") as moved:
        moved.cookies.set("op_display", cookie, path="/display")
        board = await moved.get("/display")
        poll = await moved.get("/display/live")
    async with _screen(site, "10.20.4.31") as back:
        back.cookies.set("op_display", cookie, path="/display")
        same = await back.get("/display/live")

    assert board.status_code == 403
    assert poll.status_code == 403
    assert same.status_code == 200  # the link stays valid for the right address
    page = (await site.client.get(BOARD_PAGE)).text
    assert "10.20.4.31/32" in page  # shown to the organisation admin, on the list
    for e in await _events(site):
        assert "10.20.4.3" not in f"{e.event.detail} {e.event.reason}"


async def test_a_pin_of_an_ipv6_address_is_its_64(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site, pin="on")

    async with _screen(site, "2001:db8:ab12:5:1:2:3:4") as screen:
        assert (await screen.get(f"/display/start/{token}")).status_code == 303
        cookie = screen.cookies.get("op_display", path="/display")
    async with _screen(site, "2001:db8:ab12:5:ffff::9") as privacy:
        privacy.cookies.set("op_display", cookie or "", path="/display")
        rotated = await privacy.get("/display/live")  # a privacy address of the same /64
    async with _screen(site, "2001:db8:ab12:6::1") as other:
        other.cookies.set("op_display", cookie or "", path="/display")
        elsewhere = await other.get("/display/live")

    assert rotated.status_code == 200
    assert elsewhere.status_code == 403


async def test_switching_the_organisation_switch_off_ends_every_screen(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site)
    async with _screen(site) as screen:
        await screen.get(f"/display/start/{token}")

        await _set(site, "reporting_wallboard_display_links_enabled", "false")

        poll = await screen.get("/display/live")
    assert poll.status_code == 403


async def test_a_group_link_shows_that_group_only(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site, group="demo")

    async with _screen(site) as screen:
        await screen.get(f"/display/start/{token}")
        board = await screen.get("/display")

    assert board.status_code == 200
    assert "Demo: videogesprek" in board.text


async def test_the_screen_cookie_means_nothing_to_an_admin_route(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site)
    async with _screen(site) as screen:
        await screen.get(f"/display/start/{token}")
        cookie = screen.cookies.get("op_display", path="/display")
        assert cookie is not None
        for path in (
            "/admin/groups/demo/usage?view=wallboard",
            "/admin/organisations/demo/usage?view=team",
            "/admin/organisations/demo/usage",
            "/admin/groups/demo/usage/wallboard/live",
            "/admin/",
        ):
            response = await screen.get(path, headers={"cookie": f"op_display={cookie}"})
            assert response.status_code in (303, 401, 403), path
            assert "wb__tile" not in response.text, path
            if response.status_code == 303:
                assert "/admin/login" in response.headers["location"], path


async def test_the_screen_side_has_exactly_three_routes_and_no_other_admin_page(site: Site) -> None:
    app: Any = site.app
    paths = sorted(r.path for r in all_routes(app.routes) if r.path.startswith("/display"))

    assert paths == ["/display", "/display/live", "/display/start/{token}"]


async def test_the_token_is_in_no_log_line(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)
    token = await _make(site)
    async with _screen(site) as screen:
        await screen.get(f"/display/start/{token}")
        cookie = screen.cookies.get("op_display", path="/display")
        await screen.get("/display")
        await screen.get("/display/live")

    seen = capsys.readouterr()

    assert token not in seen.out + seen.err
    assert cookie is not None
    assert cookie not in seen.out + seen.err


async def test_a_days_value_that_is_no_ascii_number_is_refused_not_a_crash(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    response = await site.client.post(
        CREATE, data={"name": "TV", "days": "\u00b2"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    assert "refused=days" in response.headers["location"]


async def test_a_post_from_another_site_is_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await _switch_on(site)

    response = await site.client.post(
        CREATE,
        data={"name": "TV", "days": "7"},
        headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
    assert TOKEN.search(response.text) is None
