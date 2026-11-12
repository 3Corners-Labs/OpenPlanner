"""The staffing screens of a counted capacity pool (ADR 0122 step 4): rights, forms, shortfalls."""

import re
from datetime import timedelta

import httpx

from app.domain.audit import Outcome
from app.domain.authorization import Scope
from app.domain.booking import BookingKind, Slot
from app.domain.capacity import CapacityPool, PoolMode
from tests.admin.conftest import (
    AUDITOR,
    GROUP_ADMIN,
    NO_ROLE,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.support.admin_site import audit_log, site_uow
from tests.support.closures import confirmed_booking
from tests.support.domain import SLOT_0900
from tests.support.skill_seed import add_pool, archive_pool

ORG = "/admin/organisations/demo/capacity"
BLOCK = {"weekday": "0", "opens": "09:00", "closes": "12:00", "capacity": "2", "offer": "scheduled"}
EVERYTHING = Scope(everything=True)


async def staffed(site: Site, name: str = "Loket", zone: str = "Europe/Amsterdam") -> str:
    """Add a counted pool, choose its time zone as the organisation admin, return its path."""
    pool = await add_pool(site, name)
    await site.login_as(ORG_ADMIN)
    response = await post(site, f"{ORG}/{pool}/timezone", {"timezone": zone})
    assert response.status_code == 303
    return f"{ORG}/{pool}"


async def post(site: Site, path: str, data: dict[str, str] | None = None) -> httpx.Response:
    """Post a same-origin form."""
    return await site.client.post(path, data=data or {}, headers=SAME_ORIGIN)


async def details(site: Site) -> list[str]:
    """Return what the capacity audit events of the pools say, oldest first."""
    return [
        e.detail
        for e in await audit_log(site)
        if e.action == "capacity.manage" and e.outcome is Outcome.SUCCESS
    ]


def ids(page: str, kind: str) -> list[int]:
    """Return the ids of the blocks or exceptions that a page links to, in page order."""
    return [int(i) for i in re.findall(rf"/{kind}/(\d+)\"", page)]


async def test_the_page_asks_for_a_time_zone_first_and_nothing_can_be_entered_before(
    site: Site,
) -> None:
    pool = await add_pool(site)
    await site.login_as(ORG_ADMIN)
    path = f"{ORG}/{pool}"

    page = (await site.client.get(f"{path}/staffing")).text
    refused = await post(site, f"{path}/staffing", BLOCK)
    exceptions = (await site.client.get(f"{path}/exceptions")).text

    assert "Kies eerst de tijdzone" in page
    assert 'id="timezone"' in page
    assert 'aria-labelledby="add-heading"' not in page  # no add-block form, no week
    assert refused.status_code == 422
    assert 'href="#timezone"' in refused.text
    assert "Kies eerst de tijdzone van deze pool" in exceptions
    assert 'aria-labelledby="add-heading"' not in exceptions


async def test_the_zone_is_saved_and_confirmed_and_a_wrong_zone_is_refused(site: Site) -> None:
    pool = await add_pool(site)
    await site.login_as(ORG_ADMIN)
    path = f"{ORG}/{pool}"

    saved = await post(site, f"{path}/timezone", {"timezone": "Europe/Amsterdam"})
    again = await post(site, f"{path}/timezone", {"timezone": "Europe/Amsterdam"})
    wrong = await post(site, f"{path}/timezone", {"timezone": "Mars/Olympus"})
    page = (await site.client.get(saved.headers["location"])).text

    assert saved.headers["location"] == f"{path}/staffing?done=saved"
    assert again.headers["location"] == f"{path}/staffing?done=unchanged"
    assert wrong.status_code == 422
    assert 'href="#timezone"' in wrong.text
    assert "De wijzigingen zijn opgeslagen." in page
    assert '<option value="Europe/Amsterdam" selected>' in page
    assert 'aria-labelledby="add-heading"' in page
    assert await details(site) == [
        "capacity_pool.created,pool:loket",
        "pool_staffing.timezone,pool:loket",
    ]


async def test_a_block_is_added_listed_changed_split_and_removed(site: Site) -> None:
    path = await staffed(site)

    added = await post(site, f"{path}/staffing", {**BLOCK, "label": "Balie", "offer": "both"})
    page = (await site.client.get(f"{path}/staffing")).text
    (block,) = ids(page, "staffing")
    form = (await site.client.get(f"{path}/staffing/{block}")).text
    changed = await post(site, f"{path}/staffing/{block}", {**BLOCK, "capacity": "3"})
    split_form = (await site.client.get(f"{path}/staffing/{block}/split")).text
    splitted = await post(
        site, f"{path}/staffing/{block}/split", {"split_at": "11:00", "capacity": "0"}
    )
    after = (await site.client.get(f"{path}/staffing")).text
    second = ids(after, "staffing")[-1]
    removed = await post(site, f"{path}/staffing/{second}/delete")

    assert added.headers["location"] == f"{path}/staffing?done=added"
    assert "09:00–12:00" in page
    assert "2 medewerkers" in page
    assert "Balie" in page
    assert 'value="09:00"' in form
    assert 'name="subject_' not in form  # a pool's block has no subjects
    assert changed.headers["location"] == f"{path}/staffing?done=saved"
    assert splitted.headers["location"] == f"{path}/staffing?done=saved"
    assert "maandag" in split_form.lower()
    assert "09:00–11:00" in after
    assert "11:00–12:00" in after
    assert "geen medewerkers" in after
    assert removed.headers["location"] == f"{path}/staffing?done=removed"
    assert "11:00–12:00" not in (await site.client.get(f"{path}/staffing")).text
    assert [d.split(",")[0] for d in (await details(site))[2:]] == [
        "pool_staffing.rule_add",
        "pool_staffing.rule_edit",
        "pool_staffing.rule_split",
        "pool_staffing.rule_remove",
    ]
    assert all("Balie" not in d for d in await details(site))


async def test_an_overlapping_block_and_a_bad_form_are_refused_with_the_inputs_kept(
    site: Site,
) -> None:
    path = await staffed(site)
    await post(site, f"{path}/staffing", BLOCK)

    overlap = await post(site, f"{path}/staffing", {**BLOCK, "opens": "11:00", "closes": "13:00"})
    bad = await post(site, f"{path}/staffing", {**BLOCK, "opens": "elf uur", "capacity": "-1"})
    backwards = await post(site, f"{path}/staffing", {**BLOCK, "opens": "12:00", "closes": "09:00"})

    for response in (overlap, bad, backwards):
        assert response.status_code == 422
        assert 'class="error-summary"' in response.text or "error-summary" in response.text
    assert 'value="11:00"' in overlap.text
    assert 'aria-invalid="true"' in bad.text
    assert len(ids((await site.client.get(f"{path}/staffing")).text, "staffing")) == 1


async def test_exceptions_are_added_listed_changed_and_removed_and_not_in_the_past(
    site: Site,
) -> None:
    path = await staffed(site)

    closed = await post(site, f"{path}/exceptions", {"day": "12-01-2026", "kind": "closed"})
    own = await post(
        site,
        f"{path}/exceptions",
        {
            "day": "13-01-2026",
            "kind": "hours",
            "opens": "10:00",
            "closes": "11:00",
            "capacity": "1",
        },
    )
    past = await post(site, f"{path}/exceptions", {"day": "31-12-2025", "kind": "closed"})
    page = (await site.client.get(f"{path}/exceptions")).text
    first, second = ids(page, "exceptions")
    form = (await site.client.get(f"{path}/exceptions/{first}")).text
    changed = await post(
        site, f"{path}/exceptions/{first}", {"day": "14-01-2026", "kind": "closed"}
    )
    removed = await post(site, f"{path}/exceptions/{second}/delete")

    assert closed.headers["location"] == f"{path}/exceptions?done=added"
    assert own.status_code == 303
    assert past.status_code == 422
    assert "Geen bezetting" in page
    assert "10:00–11:00" in page
    assert "Feestdagen uit de kalenders" in page
    assert 'id="kind-closed"' in form
    assert 'name="subject_' not in form
    assert changed.headers["location"] == f"{path}/exceptions?done=saved"
    assert removed.headers["location"] == f"{path}/exceptions?done=removed"
    assert [d.split(",")[0] for d in (await details(site))[2:]] == [
        "pool_staffing.override_add",
        "pool_staffing.override_add",
        "pool_staffing.override_edit",
        "pool_staffing.override_remove",
    ]


async def add_shortage(site: Site, pool_id: int, places: int = 2) -> None:
    """Hold ``places`` places of the pool on Monday 5 January 2026, 09:00 (Amsterdam)."""
    end = SLOT_0900 + timedelta(minutes=30)
    async with site_uow(site).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        slot = await tx.add_slot(
            Slot(
                None,
                group.id,
                SLOT_0900,
                end,
                2,
                BookingKind.SCHEDULED,
                pool_id,
                end + timedelta(minutes=5),
            )
        )
        for number in range(places):
            await tx.add_booking(
                confirmed_booking(
                    id=None,
                    group_id=group.id,
                    slot_id=slot.id,
                    token_hash=f"{number + 1:064d}",
                    public_ref=f"ref{number + 1:020d}",
                )
            )


async def test_a_shortfall_is_listed_with_the_words_that_appointments_stay(site: Site) -> None:
    path = await staffed(site)
    await post(site, f"{path}/staffing", {**BLOCK, "capacity": "1"})
    await add_shortage(site, int(path.rsplit("/", 1)[1]))

    page = (await site.client.get(f"{path}/staffing")).text
    english = (await site.client.get(f"{path}/staffing", params={"lang": "en"})).text

    assert "Tekorten vooruit" in page
    assert "maandag 5 januari 09:00–09:35: 1 ingeroosterd, 2 plekken bezet" in page
    assert "Bestaande afspraken blijven staan; er wordt niets verplaatst of geannuleerd." in page
    assert "Shortfalls ahead" in english
    assert "1 staffed, 2 places held" in english
    assert "Existing appointments stay; nothing is moved or cancelled." in english


async def test_without_a_shortfall_the_page_says_so_and_raising_the_staffing_clears_one(
    site: Site,
) -> None:
    path = await staffed(site)
    await post(site, f"{path}/staffing", {**BLOCK, "capacity": "1"})
    assert (
        "Geen tekorten in de komende 14 dagen." in (await site.client.get(f"{path}/staffing")).text
    )
    await add_shortage(site, int(path.rsplit("/", 1)[1]))
    assert "plekken bezet" in (await site.client.get(f"{path}/staffing")).text

    (block,) = ids((await site.client.get(f"{path}/staffing")).text, "staffing")
    await post(site, f"{path}/staffing/{block}", {**BLOCK, "capacity": "2"})

    assert "Geen tekorten" in (await site.client.get(f"{path}/staffing")).text


async def test_the_double_counting_notice_is_on_every_staffing_page(site: Site) -> None:
    path = await staffed(site)
    await post(site, f"{path}/staffing", BLOCK)
    (block,) = ids((await site.client.get(f"{path}/staffing")).text, "staffing")

    for page in (
        f"{path}/staffing",
        f"{path}/staffing/{block}",
        f"{path}/staffing/{block}/split",
        f"{path}/exceptions",
    ):
        assert "Let op bij het tellen" in (await site.client.get(page)).text, page


async def test_the_pool_page_and_the_list_lead_to_the_staffing(site: Site) -> None:
    path = await staffed(site)

    pool_page = (await site.client.get(path)).text
    listing = (await site.client.get("/admin/organisations/demo/skills")).text

    assert f'href="{path}/staffing"' in pool_page
    assert f'href="{path}/staffing"' in listing


async def snapshot(site: Site, path: str) -> tuple[list[int], list[int], str]:
    """Return the blocks, the exceptions and the zone the pool has now, read as the superuser."""
    await site.login_as(SUPER)
    staffing = (await site.client.get(f"{path}/staffing")).text
    exceptions = (await site.client.get(f"{path}/exceptions")).text
    zone = re.search(r'<option value="([^"]+)" selected>', staffing)
    return ids(staffing, "staffing"), ids(exceptions, "exceptions"), zone.group(1) if zone else ""


async def test_every_page_and_change_needs_capacity_manage(site: Site) -> None:
    path = await staffed(site)
    await post(site, f"{path}/staffing", BLOCK)
    await post(site, f"{path}/exceptions", {"day": "12-01-2026", "kind": "closed"})
    block = ids((await site.client.get(f"{path}/staffing")).text, "staffing")[0]
    exception = ids((await site.client.get(f"{path}/exceptions")).text, "exceptions")[0]
    routes = (
        ("GET", "/staffing"),
        ("POST", "/staffing"),
        ("POST", "/timezone"),
        ("GET", f"/staffing/{block}"),
        ("POST", f"/staffing/{block}"),
        ("GET", f"/staffing/{block}/split"),
        ("POST", f"/staffing/{block}/split"),
        ("POST", f"/staffing/{block}/delete"),
        ("GET", "/exceptions"),
        ("POST", "/exceptions"),
        ("GET", f"/exceptions/{exception}"),
        ("POST", f"/exceptions/{exception}"),
        ("POST", f"/exceptions/{exception}/delete"),
    )
    before = await snapshot(site, path)

    for who in (AUDITOR, GROUP_ADMIN, NO_ROLE, OUTSIDER):
        await site.login_as(who)
        for method, suffix in routes:
            response = await site.client.request(
                method, f"{path}{suffix}", data=BLOCK, headers=SAME_ORIGIN
            )
            assert response.status_code == 403, (who.subject, method, suffix)
    await site.login_as(SUPER)
    assert (await site.client.get(f"{path}/staffing")).status_code == 200

    assert await snapshot(site, path) == before


async def test_a_form_from_another_site_is_refused(site: Site) -> None:
    path = await staffed(site)
    before = await details(site)

    for suffix in ("/staffing", "/timezone", "/exceptions"):
        response = await site.client.post(
            f"{path}{suffix}",
            data={**BLOCK, "timezone": "UTC"},
            headers={"sec-fetch-site": "cross-site"},
        )
        assert response.status_code == 403, suffix

    assert await details(site) == before


async def test_another_organisations_archived_and_legacy_pools_have_no_staffing_pages(
    site: Site,
) -> None:
    await site.login_as(SUPER)
    theirs = await add_pool(site, "Theirs", organisation="andere")
    archived = await add_pool(site, "Oud")
    await archive_pool(site, archived)
    async with site_uow(site).read() as tx:
        legacy = next(
            p
            for p in await tx.list_capacity_pools(EVERYTHING, 1)
            if isinstance(p, CapacityPool) and p.mode is PoolMode.LEGACY_GROUP
        )
    await site.login_as(ORG_ADMIN)

    for pool in (theirs, archived, legacy.id):
        for method, suffix in (
            ("GET", "/staffing"),
            ("POST", "/staffing"),
            ("POST", "/timezone"),
            ("GET", "/staffing/1"),
            ("POST", "/staffing/1/split"),
            ("POST", "/staffing/1/delete"),
            ("GET", "/exceptions"),
            ("POST", "/exceptions"),
            ("POST", "/exceptions/1"),
            ("POST", "/exceptions/1/delete"),
        ):
            response = await site.client.request(
                method,
                f"{ORG}/{pool}{suffix}",
                data={**BLOCK, "timezone": "UTC"},
                headers=SAME_ORIGIN,
            )
            assert response.status_code == 404, (pool, method, suffix)
    assert (
        await site.client.get(f"/admin/organisations/andere/capacity/{theirs}/staffing")
    ).status_code == 403
    assert not [d for d in await details(site) if d.startswith("pool_staffing")]


async def test_a_block_of_another_pool_is_not_found_by_its_id(site: Site) -> None:
    mine = await staffed(site)
    other = await staffed(site, "Advies")
    await post(site, f"{other}/staffing", BLOCK)
    theirs = ids((await site.client.get(f"{other}/staffing")).text, "staffing")[0]

    for method, suffix in (
        ("GET", f"/staffing/{theirs}"),
        ("POST", f"/staffing/{theirs}"),
        ("GET", f"/staffing/{theirs}/split"),
        ("POST", f"/staffing/{theirs}/split"),
        ("POST", f"/staffing/{theirs}/delete"),
    ):
        response = await site.client.request(
            method, f"{mine}{suffix}", data={**BLOCK, "split_at": "10:00"}, headers=SAME_ORIGIN
        )
        assert response.status_code == 404, (method, suffix)
    assert len(ids((await site.client.get(f"{other}/staffing")).text, "staffing")) == 1


async def test_the_pages_speak_english(site: Site) -> None:
    path = await staffed(site)

    staffing = (await site.client.get(f"{path}/staffing", params={"lang": "en"})).text
    exceptions = (await site.client.get(f"{path}/exceptions", params={"lang": "en"})).text

    assert "Weekly staffing" in staffing
    assert "That many people are counted at the same time in this block." in staffing
    assert "Mind the counting." in staffing
    assert "Upcoming staffing exceptions" in exceptions
    assert "Holidays of the organisation" in exceptions


async def test_the_week_is_a_region_free_list_and_every_input_has_a_label(site: Site) -> None:
    path = await staffed(site)
    await post(site, f"{path}/staffing", BLOCK)

    page = (await site.client.get(f"{path}/staffing")).text

    inputs = re.findall(r'<(?:input|select)[^>]*\bid="([^"]+)"', page)
    assert inputs
    for name in inputs:
        assert f'for="{name}"' in page, name
    assert page.count("<h1") == 1
    assert 'class="week-frame"' in page
    assert 'data-width="wide"' in page
