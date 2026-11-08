"""The organisation's own contact-center connection on its *Kanalen* page (ADR 0032 points 9–10)."""

import json

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.webhooks import sign_wxcc
from app.domain.contact_center import ContactCenterKind
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import site_app

PAGE = "/admin/organisations/demo/channels"
FORM = {
    "cc_kind": "webex_cc",
    "cc_api_base_url": "https://api.wxcc-eu1.cisco.com",
    "cc_client_id": "C0ffee-client",
    "cc_client_secret": "client-secret-value",
    "cc_refresh_token": "refresh-token-value",
}


def _connections(site: Site) -> SealedConnections:
    connections: SealedConnections = site_app(site).state.contact_center_connections
    return connections


async def _connect(site: Site) -> None:
    await site.login_as(SUPER)
    saved = await site.client.post(f"{PAGE}/contact-center", data=FORM, headers=SAME_ORIGIN)
    assert saved.status_code == 303
    assert "done=saved" in saved.headers["location"]


async def test_the_superuser_connects_an_organisation_and_never_sees_the_secrets(
    site: Site,
) -> None:
    await _connect(site)

    page = (await site.client.get(PAGE)).text

    assert "https://api.wxcc-eu1.cisco.com" in page
    assert "C0ffee-client" in page
    assert "client-secret-value" not in page
    assert "refresh-token-value" not in page
    assert "/webhooks/contact-center/webex_cc/demo" in page
    secret = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    assert secret is not None
    assert secret not in page


async def test_a_refused_field_is_shown_on_the_form(site: Site) -> None:
    await site.login_as(SUPER)
    refused = await site.client.post(
        f"{PAGE}/contact-center",
        data=FORM | {"cc_api_base_url": "http://api.example.com"},
        headers=SAME_ORIGIN,
    )

    assert refused.status_code == 422
    assert "client-secret-value" not in refused.text
    assert 'aria-describedby="cc_api_base_url' in refused.text
    assert "Vul een https-adres in met alleen een hostnaam" in refused.text


async def test_rotating_and_removing_the_connection(site: Site) -> None:
    await _connect(site)
    before = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)

    rotated = await site.client.post(f"{PAGE}/contact-center/webhook-secret", headers=SAME_ORIGIN)
    removed = await site.client.post(f"{PAGE}/contact-center/delete", headers=SAME_ORIGIN)

    assert "done=webhook_secret_rotated" in rotated.headers["location"]
    assert "done=removed" in removed.headers["location"]
    assert before is not None
    assert await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC) is None


async def test_an_organisation_admin_may_not_set_the_connection(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await site.client.post(f"{PAGE}/contact-center", data=FORM, headers=SAME_ORIGIN)

    assert refused.status_code == 403


def _event(stamp_ms: int) -> bytes:
    return json.dumps(
        {
            "id": "evt-1",
            "type": "task:ended",
            "comciscotimestamp": stamp_ms,
            "data": {"taskId": "unknown-task"},
        },
        separators=(",", ":"),
    ).encode()


async def test_the_organisations_address_verifies_with_its_own_secret(site: Site) -> None:
    await _connect(site)
    secret = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    assert secret is not None
    stamp = int(site.clock.now().timestamp() * 1000)
    body = _event(stamp)
    address = "/webhooks/contact-center/webex_cc/demo"

    signed = await site.client.post(address, content=body, headers=sign_wxcc(body, secret, stamp))
    forged = await site.client.post(address, content=body, headers=sign_wxcc(body, "x" * 43, stamp))

    assert signed.status_code == 204  # an unknown task is acknowledged
    assert forged.status_code == 401
    for other in (
        "/webhooks/contact-center/webex_cc/andere",  # no connection of its own
        "/webhooks/contact-center/webex_cc/nobody",
        "/webhooks/contact-center/genesys/demo",
        "/webhooks/contact-center/webex_cc/Not-A-Slug",
    ):
        response = await site.client.post(
            other, content=body, headers=sign_wxcc(body, secret, stamp)
        )
        assert response.status_code == 404, other
