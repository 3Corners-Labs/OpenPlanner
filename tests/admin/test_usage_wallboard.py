"""The live wallboard as an admin sees it: complete without a script, scoped, honest about age."""

import re
from datetime import timedelta
from typing import Any

from app.domain.admin import AdminService
from app.domain.authorization import Scope
from app.domain.calls import EventKind
from app.domain.integration import IntegrationService
from app.web.static_assets import ASSETS
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
from tests.support.admin_site import site_uow as _uow
from tests.support.queue import own_queue as _own_queue

GROUP = "/admin/groups/demo/usage"
ORG = "/admin/organisations/demo/usage"
BOARD = f"{GROUP}?view=wallboard"
LIVE = f"{GROUP}/wallboard/live"


async def _guest_waits(site: Site, booking_id: int) -> None:
    uow = _uow(site)
    service = IntegrationService(uow, site.clock, AdminService(uow, site.clock))
    await service.record(booking_id, EventKind.GUEST_JOINED)


async def _alert_after(site: Site, minutes: int) -> None:
    """Set the organisation's alert limit (class D) and let the live settings see it."""
    path = "/admin/organisations/demo/settings/reporting_wallboard_wait_alert_minutes"
    response = await site.client.post(path, data={"value": str(minutes)}, headers=SAME_ORIGIN)
    assert response.status_code == 303
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    await app.state.live_settings.refresh()


async def test_the_board_is_a_report_family_of_the_usage_page(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = await site.client.get(BOARD)
    volume = (await site.client.get(GROUP)).text

    assert page.status_code == 200
    nav = page.text.split('class="report-views"', 1)[1].split("</nav>", 1)[0]
    assert f'href="{BOARD}" aria-current="page"' in nav
    assert f'href="{BOARD}"' in volume.split('class="report-views"', 1)[1].split("</nav>", 1)[0]
    assert "Wallboard" in nav


async def test_the_board_works_without_a_script_and_says_how_old_each_tile_is(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(BOARD)).text

    board = text.split('class="wb"', 1)[1]
    assert len(re.findall(r'class="wb__tile[" ]', board)) == 6
    assert board.count("per ") >= 6  # every tile says as of when
    for label in ("Wachtend nu", "Langste wachttijd", "In gesprek", "Afspraken vandaag"):
        assert label in board
    assert "Bezetting huidig blok" in board
    assert "Mislukte doorzettingen" in board
    assert f'href="{BOARD}"' in board  # the Refresh link works without the script
    assert "hidden" in board.split("data-live-pause", 1)[1][:80]  # shown by the script
    assert 'data-stale-after="3"' in text
    assert "Gegevens zijn oud sinds" in board  # in the markup, shown only by the stale state
    assert "style=" not in text.split("<main", 1)[1]


async def test_the_board_runs_the_shared_live_script_and_no_other(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(BOARD)

    scripts = re.findall(r"<script[^>]*src=\"([^\"]+)\"", response.text)
    assert ASSETS.url("live-region.js") in scripts
    assert all("wallboard" not in s for s in scripts)  # no second polling mechanism
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe" not in response.headers["content-security-policy"]
    poll = re.search(r'data-poll="([^"]+)"', response.text)
    assert poll is not None
    assert poll.group(1) == LIVE
    assert 'data-poll-seconds="10"' in response.text


async def test_the_kiosk_layout_has_no_menu_and_the_same_board(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    kiosk = (await site.client.get(f"{BOARD}&layout=kiosk")).text

    assert "admin-nav" not in kiosk
    assert "report-views" not in kiosk
    assert kiosk.count("<h1") == 1
    assert 'class="wb__tile"' in kiosk
    assert f'href="{BOARD}"' in kiosk  # the way back
    assert 'data-theme="dark"' in kiosk


async def test_the_kiosk_needs_the_same_permission(site: Site) -> None:
    await site.login_as(AUDITOR)

    refused = await site.client.get(f"{BOARD}&layout=kiosk")

    assert refused.status_code == 403
    assert "wb__tile" not in refused.text


async def test_who_may_open_the_board(site: Site) -> None:
    allowed = {ORG_ADMIN: 200, GROUP_ADMIN: 200, SUPER: 200, AUDITOR: 403, AGENT: 403}

    for who, status in allowed.items():
        await site.login_as(who)
        assert (await site.client.get(BOARD)).status_code == status, who.subject
        assert (await site.client.get(LIVE)).status_code == status, who.subject
    await site.login_as(OUTSIDER)
    assert (await site.client.get(BOARD)).status_code == 403
    assert (await site.client.get(f"{ORG}?view=wallboard")).status_code == 403


async def test_a_refusal_is_audited_and_the_menu_link_is_not_offered(site: Site) -> None:
    await site.login_as(AUDITOR)

    volume = (await site.client.get(GROUP)).text
    await site.client.get(BOARD)

    nav = volume.split('class="report-views"', 1)[1].split("</nav>", 1)[0]
    assert "view=wallboard" not in nav
    async with _uow(site).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=5)
    assert any(e.event.action == "reports.wallboard.view" for e in events)


async def test_the_organisation_board_covers_its_groups(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(f"{ORG}?view=wallboard")).text

    assert 'class="wb__tile"' in page
    assert f'data-poll="{ORG}/wallboard/live"' in page


async def test_the_fragment_is_the_board_alone_and_never_204(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    fragment = await site.client.get(f"{LIVE}?seen=anything")
    again = await site.client.get(LIVE)

    assert fragment.status_code == 200  # never 204: the clock moves each time
    assert again.status_code == 200
    assert "<html" not in fragment.text
    assert "data-live-fragment" in fragment.text
    assert fragment.headers["cache-control"] == "no-store"
    assert "Wachtend nu" in fragment.text


async def test_no_own_queue_says_live_figures_are_not_shown(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(LIVE)).text

    assert "Live getallen zie je voor diensten met een eigen wachtrij" in text


async def test_a_waiting_participant_is_counted_exactly_and_a_long_wait_has_words(
    site: Site,
) -> None:
    booking_id = await _own_queue(site)
    await site.login_as(GROUP_ADMIN)
    await _guest_waits(site, booking_id)

    calm = (await site.client.get(LIVE)).text
    site.clock.advance(timedelta(minutes=6))
    long = (await site.client.get(LIVE)).text

    assert re.search(r"wb__n\">1<", calm)
    assert (
        "lange wachttijd"
        not in calm.split("data-live-stale-status", 1)[0].lower().replace("reports", "")
        or "wb__tile--alert" not in calm
    )
    assert "wb__tile--alert" not in calm
    assert "wb__tile--alert" in long
    assert '<strong class="wb__tag">lange wachttijd</strong>' in long
    assert "6:00" in long  # the longest wait, as a clock reads
    # announced once, through the polite region, by the fragment's status sentence
    assert "Lange wachttijd: iemand wacht langer dan de grens" in long
    status = long.split("data-live-status hidden>", 1)[1].split("</span>", 1)[0]
    assert "Lange wachttijd" in status
    assert "Lange wachttijd" not in calm.split("data-live-status hidden>", 1)[1].split("<", 1)[0]


async def test_the_organisation_board_counts_the_live_queue_of_its_groups(site: Site) -> None:
    """An organisation admin holds the board and the queue right on every group of it."""
    booking_id = await _own_queue(site)
    await _guest_waits(site, booking_id)
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get(f"{ORG}/wallboard/live")).text

    assert re.search(r"wb__n\">1<", text)


async def test_a_group_admin_is_not_offered_the_organisation_board(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    org_page = await site.client.get(f"{ORG}")

    assert org_page.status_code == 403  # the organisation's page adds up its groups
    nav_page = (await site.client.get(GROUP)).text
    assert "view=wallboard" in nav_page  # the group's own board is offered


async def test_no_name_reference_or_link_reaches_the_board(site: Site) -> None:
    booking_id = await _own_queue(site)
    await _guest_waits(site, booking_id)
    await site.login_as(GROUP_ADMIN)

    for url in (BOARD, f"{BOARD}&layout=kiosk", LIVE):
        text = (await site.client.get(url)).text
        assert "Ada" not in text
        assert "Lovelace" not in text
        assert "ada@example.org" not in text
        assert "webex.example" not in text


async def test_a_bad_layout_is_the_framed_page(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    page = (await site.client.get(f"{BOARD}&layout=weird")).text

    assert "report-views" in page


async def test_the_organisation_sets_when_a_wait_is_long(site: Site) -> None:
    booking_id = await _own_queue(site)
    await _guest_waits(site, booking_id)
    await site.login_as(ORG_ADMIN)
    await _alert_after(site, 10)

    site.clock.advance(timedelta(minutes=6))
    text = (await site.client.get(f"{ORG}/wallboard/live")).text

    assert "6:00" in text
    assert "wb__tile--alert" not in text  # six minutes is under this organisation's ten
