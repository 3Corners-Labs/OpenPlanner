"""The organisation's own contact-center connection on its *Kanalen* page (ADR 0032 points 9–10)."""

import json
from dataclasses import replace

import httpx

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


# --- Allowed source networks (ADR 0124 point 3, step 2b) --------------------------------------

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
    transport = httpx.ASGITransport(app=site_app(site), client=(address, 4711))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.post(ADDRESS, content=body, headers=sign_wxcc(body, secret, stamp))


def _set_settings(site: Site, **update: object) -> None:
    state = site_app(site).state
    state.settings = state.settings.model_copy(update=update)


async def _webhook_refusals(site: Site) -> list[tuple[str, str]]:
    log = await site_app(site).state.admin_service.audit_log(SUPER)
    return sorted(
        (
            e.event.reason,
            ",".join(k for k in e.event.detail.split(",") if not k.startswith("client:")),
        )
        for e in log
        if e.event.action == "webhook.rejected"
    )


async def test_the_allowed_networks_are_edited_on_the_card_and_kept_canonical(site: Site) -> None:
    await _connect(site)
    assert 'name="cc_allowed_networks"' in (await site.client.get(PAGE)).text

    saved = await _save_networks(site, f"8.8.8.9/24\n\n2001:4860:4860::1/48\n{SUPPLIER_RANGE}")

    assert saved.status_code == 303
    page = (await site.client.get(PAGE)).text
    assert f"{SUPPLIER_RANGE}\n2001:4860:4860::/48</textarea>" in page
    assert "Beperkt tot 2 netwerk(en), uit de eigen lijst van deze koppeling" in page
    stored = await _connections(site).get(1)
    assert stored is not None
    assert stored.allowed_networks == (SUPPLIER_RANGE, "2001:4860:4860::/48")


async def test_changing_the_networks_alone_keeps_the_webhook_secret_and_registers_nothing(
    site: Site,
) -> None:
    """The list is not part of the subscription: no new secret, no new registration."""
    await _connect(site)
    registrar = _registrar(site)
    before = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)

    saved = await _save_networks(site, SUPPLIER_RANGE)

    assert "done=contact_center_saved" in saved.headers["location"]
    assert registrar.registered == []
    assert await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC) == before
    audit = await site_app(site).state.admin_service.audit_log(SUPER)
    saves = [
        e.event.detail for e in audit if e.event.detail.startswith("contact_center_connection.save")
    ]
    assert saves[0] == "contact_center_connection.save,kind:webex_cc,allowed_networks"  # newest
    assert not any("8.8.8" in e.event.detail for e in audit)


async def test_a_refused_list_is_shown_at_its_input_and_stores_nothing(site: Site) -> None:
    await _connect(site)

    refused = await _save_networks(site, "8.8.8.0/24\nnot-a-network")

    assert refused.status_code == 422
    assert 'aria-describedby="cc_allowed_networks' in refused.text
    assert "Vul netwerken in als CIDR" in refused.text
    stored = await _connections(site).get(1)
    assert stored is not None
    assert stored.allowed_networks == ()


async def test_the_card_never_leaves_the_state_of_the_address_silent(site: Site) -> None:
    await _connect(site)
    open_card = (await site.client.get(PAGE)).text
    _set_settings(site, webhook_source_networks_required=True)
    required = (await site.client.get(PAGE)).text
    _set_settings(site, webhook_source_networks_contact_center=SUPPLIER_RANGE)
    default = (await site.client.get(PAGE)).text
    await _save_networks(site, "2001:4860:4860::/48")
    own = (await site.client.get(PAGE)).text

    assert "Niet beperkt: elke bron mag dit adres aanroepen" in open_card
    assert (
        "Een lijst is verplicht en ontbreekt: dit adres accepteert geen enkele aanroep" in required
    )
    assert "Beperkt tot 1 netwerk(en), uit de standaardlijst van de installatie" in default
    assert "Beperkt tot 1 netwerk(en), uit de eigen lijst van deze koppeling" in own


async def test_a_list_on_a_chain_that_cannot_be_trusted_is_shown_as_refused_with_the_repair(
    site: Site,
) -> None:
    await _connect(site)
    await _save_networks(site, SUPPLIER_RANGE)
    _set_settings(site, forwarded_allow_ips="*")

    page = (await site.client.get(PAGE)).text

    assert "Geweigerd" in page
    assert "Zet het alleen op het eigen adres van de proxy" in page


async def test_the_organisations_own_list_governs_its_address_and_a_stranger_gets_a_403(
    site: Site,
) -> None:
    await _connect(site)
    await _save_networks(site, SUPPLIER_RANGE)

    inside = await _call_from(site, SUPPLIER)
    outside = await _call_from(site, STRANGER)

    assert inside.status_code == 204
    assert (outside.status_code, outside.content) == (403, b"")
    assert await _webhook_refusals(site) == [("network", "source:wxcc,organisation:demo")]


async def test_an_own_list_replaces_the_installations_default_and_without_one_the_default_applies(
    site: Site,
) -> None:
    await _connect(site)
    _set_settings(site, webhook_source_networks_contact_center=SUPPLIER_RANGE)

    default_in = await _call_from(site, SUPPLIER)
    default_out = await _call_from(site, STRANGER)
    await _save_networks(site, "8.8.4.0/24")
    own_in = await _call_from(site, STRANGER)
    own_out = await _call_from(site, SUPPLIER)  # inside the default, outside the own list
    await _save_networks(site, "")
    back_to_default = await _call_from(site, STRANGER)

    assert [r.status_code for r in (default_in, default_out)] == [204, 403]
    assert [r.status_code for r in (own_in, own_out)] == [204, 403]
    assert back_to_default.status_code == 403


async def test_a_required_list_refuses_an_organisation_without_one_and_a_list_opens_it(
    site: Site,
) -> None:
    await _connect(site)
    _set_settings(site, webhook_source_networks_required=True)

    refused = await _call_from(site, SUPPLIER)
    await _save_networks(site, SUPPLIER_RANGE)
    accepted = await _call_from(site, SUPPLIER)

    assert (refused.status_code, accepted.status_code) == (403, 204)
    assert await _webhook_refusals(site) == [("network", "source:wxcc,organisation:demo")]


async def test_a_co_tenant_inside_the_range_still_fails_the_signature_at_the_organisation(
    site: Site,
) -> None:
    await _connect(site)
    await _save_networks(site, SUPPLIER_RANGE)

    forged = await _call_from(site, SUPPLIER, secret="x" * 43)

    assert (forged.status_code, forged.content) == (401, b"")
