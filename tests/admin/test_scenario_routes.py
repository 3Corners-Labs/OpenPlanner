"""Closure scenario screens (ADR 0037 §4, CL-4): prepare, use, and the limits of who may change.

The organisation admin prepares organisation scenarios; a group admin prepares the team's own
and uses the organisation's, read-only. "Start closure" picks a scenario and copies it.
"""

import re

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, Site
from tests.admin.test_closure_routes import CLOSURE, _book
from tests.support.images import SECRET, jpeg, png

ORG = "/admin/organisations/demo/closure/scenarios"
GROUP = "/admin/groups/demo/closure/scenarios"
STRIKE = {
    "scenario_name": "Staking",
    "notice_nl": "Wegens een staking zijn wij gesloten.",
    "notice_en": "We are closed because of a strike.",
    "heading_nl": "Staking",
}
OUTAGE = {
    "scenario_name": "Storing",
    "notice_nl": "Door een storing zijn wij gesloten.",
    "notice_en": "We are closed because of an outage.",
}
CONTACTS = {"contact_1_kind": "phone", "contact_1_value": "14 020"}


async def _details(site: Site) -> list[str]:
    """Return the detail of every audit event, oldest first."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=50)
    return [e.event.detail for e in reversed(events)]


async def _post(site: Site, path: str, data: dict[str, str] | None = None) -> httpx.Response:
    return await site.client.post(path, data=data or {}, headers=SAME_ORIGIN)


async def _create(site: Site, base: str, data: dict[str, str]) -> str:
    """Create a scenario and return its page."""
    response = await _post(site, base, data)
    assert response.status_code == 303, response.text
    match = re.match(
        rf"({re.escape(base)}/\d+)\?done=scenario_created", response.headers["location"]
    )
    assert match
    return match.group(1)


async def test_organisation_admin_prepares_a_scenario_with_contacts_and_image(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    page = await _create(site, ORG, STRIKE)

    contacts = await _post(site, f"{page}/contacts", CONTACTS)
    image = await site.client.post(
        f"{page}/image",
        data={"alt_nl": "Gesloten deur", "alt_en": "Closed door"},
        files={"image": ("deur.jpg", jpeg(), "image/jpeg")},
        headers=SAME_ORIGIN,
    )

    assert contacts.headers["location"] == f"{page}?done=saved"
    assert image.headers["location"] == f"{page}?done=image_saved"
    shown = (await site.client.get(page)).text
    assert "Wegens een staking zijn wij gesloten." in shown
    assert 'href="tel:' in shown
    src = re.search(r'<img class="closure-image" src="([^"]+)" alt="Gesloten deur"', shown)
    assert src
    served = await site.client.get(src.group(1))
    assert served.status_code == 200
    assert served.headers["cache-control"] == "private, no-store"
    assert SECRET not in served.content
    listed = (await site.client.get(ORG)).text
    assert "Staking" in listed
    details = await _details(site)
    assert any(d.startswith("closure.scenario.contacts,") and "phone:1" in d for d in details)
    assert not [d for d in details if "Staking" in d or "14 020" in d or "deur" in d.lower()]


async def test_a_scenario_image_is_not_public(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    page = await _create(site, ORG, STRIKE)
    await site.client.post(
        f"{page}/image",
        data={"decorative": "yes"},
        files={"image": ("x.png", png(), "image/png")},
        headers=SAME_ORIGIN,
    )
    await site.client.post("/admin/logout", headers=SAME_ORIGIN)

    anonymous = await site.client.get(f"{page}/image")
    public = await site.client.get("/g/demo/closure/image")

    assert anonymous.status_code in {303, 401, 403}
    assert public.status_code == 404


async def test_a_group_admin_uses_but_cannot_change_an_organisation_scenario(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    org_page = await _create(site, ORG, STRIKE)
    scenario_id = org_page.rsplit("/", 1)[1]
    await site.login_as(GROUP_ADMIN)

    listed = (await site.client.get(GROUP)).text
    view = await site.client.get(f"{GROUP}/{scenario_id}")
    change = await _post(site, f"{GROUP}/{scenario_id}", OUTAGE)
    delete = await _post(site, f"{GROUP}/{scenario_id}/delete")
    contacts = await site.client.get(f"{GROUP}/{scenario_id}/contacts")
    via_organisation = await _post(site, f"{ORG}/{scenario_id}", OUTAGE)

    assert "Staking" in listed
    assert view.status_code == 200
    assert "Wegens een staking" in view.text
    assert f'action="{GROUP}/{scenario_id}"' not in view.text  # no forms, a preview only
    assert (change.status_code, delete.status_code, contacts.status_code) == (404, 404, 404)
    assert via_organisation.status_code == 403
    await site.login_as(ORG_ADMIN)
    assert "Wegens een staking" in (await site.client.get(org_page)).text


async def test_start_closure_picks_a_scenario_and_copies_it(site: Site) -> None:
    manage = await _book(site)
    await site.login_as(GROUP_ADMIN)
    page = await _create(site, GROUP, STRIKE)
    scenario_id = page.rsplit("/", 1)[1]
    await _post(site, f"{page}/contacts", CONTACTS)

    form = (await site.client.get(CLOSURE, params={"scenario": scenario_id})).text
    assert f'<input type="hidden" name="scenario_id" value="{scenario_id}">' in form
    assert "Wegens een staking zijn wij gesloten.</textarea>" in form
    started = await _post(
        site,
        CLOSURE,
        {**STRIKE, "notice_nl": "Vandaag staking.", "scenario_id": scenario_id},
    )
    assert started.status_code == 303
    # Later edits of the scenario leave the running closure alone.
    await _post(site, page, OUTAGE)
    await _post(site, f"{page}/contacts", {})

    public = (await site.client.get("/g/demo")).text
    assert "Vandaag staking." in public
    assert "Door een storing" not in public
    assert 'href="tel:' in public
    assert "Opgeschort" in (await site.client.get(manage)).text
    details = await _details(site)
    assert f"closure.start,scenario:{scenario_id},bookings:1,contacts:1" in details


async def test_an_unknown_or_foreign_scenario_cannot_start_a_closure(site: Site) -> None:
    await site.login_as(OUTSIDER)
    foreign = await _create(site, "/admin/organisations/andere/closure/scenarios", STRIKE)
    foreign_id = foreign.rsplit("/", 1)[1]
    await site.login_as(GROUP_ADMIN)

    picked = await site.client.get(CLOSURE, params={"scenario": foreign_id})
    started = await _post(site, CLOSURE, {**OUTAGE, "scenario_id": foreign_id})

    assert picked.status_code == 404
    assert started.status_code == 404
    assert "Tijdelijk gesloten" not in (await site.client.get("/g/demo")).text


async def test_the_organisation_closes_every_group_with_one_scenario(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    page = await _create(site, ORG, STRIKE)
    scenario_id = page.rsplit("/", 1)[1]

    form = (
        await site.client.get("/admin/organisations/demo/closure", params={"scenario": scenario_id})
    ).text
    closed = await _post(
        site, "/admin/organisations/demo/closure", {**STRIKE, "scenario_id": scenario_id}
    )

    assert f'name="scenario_id" value="{scenario_id}"' in form
    assert closed.status_code == 303
    assert "Wegens een staking zijn wij gesloten." in (await site.client.get("/g/demo")).text


async def test_scenario_errors_are_linked_to_their_fields(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    missing = await _post(site, GROUP, {"scenario_name": "", "notice_nl": "Dicht"})
    page = await _create(site, GROUP, OUTAGE)
    bad_link = await _post(
        site, f"{page}/contacts", {"contact_1_kind": "website", "contact_1_value": "http://x.nl"}
    )
    svg = await site.client.post(
        f"{page}/image",
        data={"alt_nl": "x", "alt_en": "x"},
        files={"image": ("x.png", b"<svg onload=alert(1)>", "image/png")},
        headers=SAME_ORIGIN,
    )

    assert missing.status_code == 422
    assert '<a href="#scenario_name">' in missing.text
    assert bad_link.status_code == 422
    assert '<a href="#contact_1_value">' in bad_link.text
    assert svg.status_code == 422
    assert '<a href="#image">' in svg.text


async def test_deleting_a_scenario_keeps_a_running_closure(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    page = await _create(site, GROUP, STRIKE)
    scenario_id = page.rsplit("/", 1)[1]
    await site.client.post(
        f"{page}/image",
        data={"alt_nl": "Deur", "alt_en": "Door"},
        files={"image": ("x.png", png(), "image/png")},
        headers=SAME_ORIGIN,
    )
    await _post(site, CLOSURE, {**STRIKE, "scenario_id": scenario_id})

    deleted = await _post(site, f"{page}/delete")

    assert deleted.headers["location"] == f"{GROUP}?done=scenario_deleted"
    assert (await site.client.get(page)).status_code == 404
    public = (await site.client.get("/g/demo")).text
    assert "Wegens een staking" in public
    assert 'alt="Deur"' in public
    src = re.search(r'<img class="closure-image" src="([^"]+)"', public)
    assert src
    assert (await site.client.get(src.group(1))).status_code == 200
