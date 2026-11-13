"""The organisation's own contact-center connection on its *Kanalen* page (ADR 0032 points 9–10)."""

import json
from dataclasses import replace

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.webhooks import sign_wxcc
from app.domain.contact_center import ContactCenterKind, Registration
from app.web.protection import RateLimits, configure_rate_limits
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.fakes.integrations import FakeOrganisationDispatchers
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


def _registrar(
    site: Site, outcome: Registration = Registration.REGISTERED
) -> FakeOrganisationDispatchers:
    """Put a fake in place of the platform: no test talks to a real contact center."""
    registrar = FakeOrganisationDispatchers(outcome=outcome)
    state = site_app(site).state
    state.integrations = replace(state.integrations, organisations=registrar)
    return registrar


async def _connect(site: Site) -> None:
    _registrar(site)
    await site.login_as(SUPER)
    saved = await site.client.post(f"{PAGE}/contact-center", data=FORM, headers=SAME_ORIGIN)
    assert saved.status_code == 303
    assert "done=contact_center_registered" in saved.headers["location"]


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
    _registrar(site)
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


async def test_a_refused_registration_says_so_and_keeps_the_new_secret(site: Site) -> None:
    """ADR 0032 CC-4d: the platform's refusal is shown; the change itself stays."""
    await _connect(site)
    before = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    _registrar(site, Registration.REFUSED)

    refused = await site.client.post(f"{PAGE}/contact-center/webhook-secret", headers=SAME_ORIGIN)

    assert "done=contact_center_not_registered" in refused.headers["location"]
    assert await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC) != before


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


async def test_a_refusal_at_an_organisations_address_names_the_organisation_and_only_a_real_one(
    site: Site,
) -> None:
    """ADR 0124 point 10: the slug is public; a slug the caller made up is never written."""
    await _connect(site)
    secret = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    assert secret is not None
    stamp = int(site.clock.now().timestamp() * 1000)
    body = _event(stamp)
    forged = sign_wxcc(body, "x" * 43, stamp)

    for address in (
        "/webhooks/contact-center/webex_cc/demo",  # a wrong signature
        "/webhooks/contact-center/webex_cc/andere",  # exists, no connection
        "/webhooks/contact-center/webex_cc/nobody",  # does not exist
    ):
        response = await site.client.post(address, content=body, headers=forged)
        assert response.content == b""
    refused = await site.client.post(
        "/webhooks/contact-center/webex_cc/demo",
        content=body,
        headers=sign_wxcc(body, secret, stamp) | {"content-type": "text/plain"},
    )
    assert (refused.status_code, refused.content) == (415, b"")

    log = await site_app(site).state.admin_service.audit_log(SUPER)
    refusals = sorted(
        (
            e.event.reason,
            ",".join(k for k in e.event.detail.split(",") if not k.startswith("client:")),
        )
        for e in log
        if e.event.action == "webhook.rejected"
    )
    assert refusals == [
        ("content_type", "source:wxcc,organisation:demo"),
        ("invalid_signature", "source:wxcc,organisation:demo"),
        ("unknown_address", "source:wxcc"),  # "nobody" does not exist: its slug is not written
        ("unknown_address", "source:wxcc,organisation:andere"),
    ]
    assert "nobody" not in str(refusals)


async def test_forged_calls_to_an_organisation_never_lock_its_signed_calls_out(site: Site) -> None:
    """ADR 0124 point 5: a customer of the same supplier cannot use up an organisation's budget."""
    await _connect(site)
    secret = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    assert secret is not None
    stamp = int(site.clock.now().timestamp() * 1000)
    body = _event(stamp)
    address = "/webhooks/contact-center/webex_cc/demo"
    configure_rate_limits(RateLimits(webhooks="3/minute"), enabled=True)
    try:
        forged = [
            (
                await site.client.post(
                    address, content=body, headers=sign_wxcc(body, "x" * 43, stamp)
                )
            )
            for _ in range(5)
        ]
        signed = await site.client.post(
            address, content=body, headers=sign_wxcc(body, secret, stamp)
        )
        other_organisation = await site.client.post(
            "/webhooks/contact-center/webex_cc/andere", content=body
        )
    finally:
        configure_rate_limits(RateLimits(), enabled=False)

    assert [r.status_code for r in forged] == [401, 401, 401, 429, 429]
    assert signed.status_code == 204
    assert other_organisation.status_code == 404
