"""Helpers the tests of an organisation's contact-center connection share (ADR 0032, ADR 0124).

The card's form, a connected organisation, the signed calls to its webhook address from a given
source address, and the pause button. Tests of the card, of the gate at an organisation's address
and of its delivery status use them; they live here because no test imports a test module.
"""

import json
from dataclasses import replace

import httpx

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.webhooks import sign_wxcc
from app.domain.contact_center import ContactCenterKind, Registration
from tests.fakes.integrations import FakeOrganisationDispatchers
from tests.support.admin_site import SAME_ORIGIN, SUPER, Site

PAGE = "/admin/organisations/demo/channels"


FORM = {
    "cc_kind": "webex_cc",
    "cc_api_base_url": "https://api.wxcc-eu1.cisco.com",
    "cc_client_id": "C0ffee-client",
    "cc_client_secret": "client-secret-value",
    "cc_refresh_token": "refresh-token-value",
}


def _connections(site: Site) -> SealedConnections:
    connections: SealedConnections = site.app.state.contact_center_connections
    return connections


def _registrar(
    site: Site, outcome: Registration = Registration.REGISTERED
) -> FakeOrganisationDispatchers:
    """Put a fake in place of the platform: no test talks to a real contact center."""
    registrar = FakeOrganisationDispatchers(outcome=outcome)
    state = site.app.state
    state.integrations = replace(state.integrations, organisations=registrar)
    return registrar


async def _connect(site: Site) -> None:
    _registrar(site)
    await site.login_as(SUPER)
    saved = await site.client.post(f"{PAGE}/contact-center", data=FORM, headers=SAME_ORIGIN)
    assert saved.status_code == 303
    assert "done=contact_center_registered" in saved.headers["location"]


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


SUPPLIER = "8.8.8.8"


SUPPLIER_RANGE = "8.8.8.0/24"


STRANGER = "8.8.4.4"


ADDRESS = "/webhooks/contact-center/webex_cc/demo"


async def _save_networks(site: Site, text: str, **form: str) -> httpx.Response:
    """Save the card with only the allowed networks changed (secrets left blank: they are kept)."""
    data = FORM | {"cc_client_secret": "", "cc_refresh_token": ""} | {"cc_allowed_networks": text}
    return await site.client.post(f"{PAGE}/contact-center", data=data | form, headers=SAME_ORIGIN)


async def _call_from(site: Site, address: str, secret: str | None = None) -> httpx.Response:
    """Send a task event to the organisation's address from ``address``.

    It is signed correctly unless a ``secret`` (a wrong one) is given.
    """
    secret = secret or await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    assert secret is not None
    stamp = int(site.clock.now().timestamp() * 1000)
    body = _event(stamp)
    async with site.browser(address).client as c:
        return await c.post(ADDRESS, content=body, headers=sign_wxcc(body, secret, stamp))


def _set_settings(site: Site, **update: object) -> None:
    state = site.app.state
    state.settings = state.settings.model_copy(update=update)


async def _pause(site: Site, state: str, org: str = "demo") -> httpx.Response:
    """Press the card's button: ``off`` pauses the deliveries, ``on`` resumes them."""
    return await site.client.post(
        f"/admin/organisations/{org}/channels/contact-center/inbound",
        data={"inbound": state},
        headers=SAME_ORIGIN,
    )
