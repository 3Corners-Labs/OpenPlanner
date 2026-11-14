"""The digest subscriptions on the organisation's Usage page (ADR 0117 point 16)."""

from typing import Any

from app.domain.authorization import Scope
from tests.support.admin_site import AUDITOR, GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site, site_uow

PAGE = "/admin/organisations/demo/usage"
FORM = {**SAME_ORIGIN, "content-type": "application/x-www-form-urlencoded"}


async def _staff_id(site: Site, subject: str) -> int:
    async with site_uow(site).read() as tx:
        member = await tx.get_staff(subject)
    assert member is not None
    assert member.id is not None
    return member.id


async def _subscribe(site: Site, body: str) -> Any:
    return await site.client.post(f"{PAGE}/digest", content=body, headers=FORM)


async def _world(site: Site) -> int:
    """Let the group admin log in once, so a staff record exists; return its row id."""
    await site.login_as(GROUP_ADMIN)
    gina = await _staff_id(site, GROUP_ADMIN.subject)
    await site.login_as(ORG_ADMIN)
    return gina


async def test_the_org_admin_sees_the_section_and_a_reader_does_not(site: Site) -> None:
    await _world(site)
    page = (await site.client.get(PAGE)).text
    await site.login_as(AUDITOR)
    reader = (await site.client.get(PAGE)).text

    assert 'id="digest"' in page
    assert "Samenvatting per e-mail" in page
    assert 'id="digest"' not in reader


async def test_a_subscription_is_added_listed_audited_and_deleted(site: Site) -> None:
    gina = await _world(site)

    added = await _subscribe(
        site, f"group=demo&period=weekly&weekday=2&month_day=1&recipient={gina}"
    )
    page = (await site.client.get(PAGE)).text
    async with site_uow(site).read() as tx:
        sub = (await tx.list_subscriptions(Scope(everything=True)))[0]
        events = await tx.list_audit_events(Scope(everything=True))
    removed = await site.client.post(f"{PAGE}/digest/{sub.id}/delete", content="", headers=FORM)

    assert added.status_code == 303
    assert "woensdag" in page
    assert [e.event.detail for e in events if e.event.action == "reports.digest.manage"] == [
        "create:weekly"
    ]
    assert removed.status_code == 303
    assert "Er zijn nog geen abonnementen." in (await site.client.get(PAGE)).text


async def test_a_recipient_who_may_not_read_or_none_at_all_is_refused_on_the_page(
    site: Site,
) -> None:
    await _world(site)

    none = await _subscribe(site, "group=demo&period=daily&weekday=0&month_day=1")
    foreign = await _subscribe(site, "group=demo&period=daily&weekday=0&month_day=1&recipient=999")
    bad = await _subscribe(site, "period=hourly&recipient=1")

    assert none.status_code == foreign.status_code == bad.status_code == 422
    assert "Kies minstens één collega" in none.text
    assert "Kies een bestaande periode" in bad.text


async def test_the_form_needs_the_same_origin_and_the_permission(site: Site) -> None:
    gina = await _world(site)

    cross = await site.client.post(
        f"{PAGE}/digest",
        content=f"period=daily&recipient={gina}",
        headers={
            "sec-fetch-site": "cross-site",
            "content-type": "application/x-www-form-urlencoded",
        },
    )
    await site.login_as(GROUP_ADMIN)
    denied = await _subscribe(site, f"period=daily&recipient={gina}")

    assert cross.status_code == 403
    assert denied.status_code == 403
