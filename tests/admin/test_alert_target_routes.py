"""Alert targets on Koppelingen and on an organisation's Kanalen page (ADR 0082 point 9)."""

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.support.admin_site import ORG_ADMIN, SAME_ORIGIN, SUPER, Site


async def _details(site: Site) -> list[str]:
    app = site.app
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=50)
    return [e.event.detail for e in reversed(events) if e.event.detail.startswith("alert_")]


async def test_a_platform_target_is_added_tested_and_removed(site: Site) -> None:
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/integrations")).text
    assert "Meldingen aan beheerders" in page
    assert 'action="/admin/integrations/alerts/targets"' in page
    added = await site.client.post(
        "/admin/integrations/alerts/targets",
        data={"kind": "email", "address": "ops@voorbeeld.nl", "min_level": "outage"},
        headers=SAME_ORIGIN,
    )
    assert added.headers["location"] == "/admin/integrations?done=alert_target_added#alerts"
    page = (await site.client.get("/admin/integrations")).text
    assert "<code>ops@voorbeeld.nl</code>" in page
    assert "Alleen storingen" in page
    target_id = page.split("/admin/integrations/alerts/targets/")[1].split("/")[0]
    tested = await site.client.post(
        f"/admin/integrations/alerts/targets/{target_id}/test", headers=SAME_ORIGIN
    )
    assert "done=alert_test" in tested.headers["location"]
    refused = await site.client.post(
        "/admin/integrations/alerts/targets",
        data={"kind": "email", "address": "not an address", "min_level": "warning"},
        headers=SAME_ORIGIN,
    )
    assert "done=alert_refused" in refused.headers["location"]
    removed = await site.client.post(
        f"/admin/integrations/alerts/targets/{target_id}/remove", headers=SAME_ORIGIN
    )
    assert "done=alert_target_removed" in removed.headers["location"]
    details = await _details(site)
    assert details[0] == f"alert_target.add,id:{target_id},kind:email"
    assert any(d.startswith(f"alert_target.test,id:{target_id},outcome:") for d in details)
    assert not any("ops@" in d for d in details)


async def test_an_organisations_targets_live_on_its_channels_page(site: Site) -> None:
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/organisations/demo/channels")).text
    assert 'action="/admin/organisations/demo/channels/alerts/targets"' in page
    assert "Webex-bot" in page
    empty = await site.client.post(
        "/admin/organisations/demo/channels/alerts/bot", data={"token": ""}, headers=SAME_ORIGIN
    )
    assert "done=unchanged" in empty.headers["location"]  # an empty field removes nothing
    await site.login_as(ORG_ADMIN)
    refused = await site.client.post(
        "/admin/organisations/demo/channels/alerts/targets",
        data={"kind": "email", "address": "ops@voorbeeld.nl", "min_level": "warning"},
        headers=SAME_ORIGIN,
    )
    assert refused.status_code == 403
