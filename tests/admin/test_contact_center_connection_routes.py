"""The organisation's own contact-center connection on its *Kanalen* page (ADR 0032 points 9–10)."""

import pytest
import structlog.testing

from app.adapters.db.credentials import CredentialStore
from app.adapters.db.record_names import contact_center_credential_name
from app.adapters.integrations import sealed_store
from app.adapters.webhooks import sign_wxcc
from app.domain.contact_center import ContactCenterKind, Registration
from app.domain.integration import EventOutcome
from app.domain.networks import NetworkList
from app.web.protection import RateLimits, configure_rate_limits
from tests.support.admin_site import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.contact_center_site import (
    FORM,
    PAGE,
    STRANGER,
    SUPPLIER,
    SUPPLIER_RANGE,
    _call_from,
    _connect,
    _connections,
    _event,
    _pause,
    _registrar,
    _save_networks,
    _set_settings,
)


def _credentials(site: Site) -> CredentialStore:
    """The sealed store the connections live in, as the application builds it."""
    state = site.app.state
    store = sealed_store(state.engine, state.settings)
    assert store is not None
    return store


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

    log = await site.app.state.admin_service.audit_log(SUPER)
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


async def _webhook_refusals(site: Site) -> list[tuple[str, str]]:
    log = await site.app.state.admin_service.audit_log(SUPER)
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
    assert stored.allowed_networks == NetworkList((SUPPLIER_RANGE, "2001:4860:4860::/48"))


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
    audit = await site.app.state.admin_service.audit_log(SUPER)
    saves = [
        e.event.detail for e in audit if e.event.detail.startswith("contact_center_connection.save")
    ]
    assert saves[0] == "contact_center_connection.save,kind:webex_cc,allowed_networks"  # newest
    assert not any("8.8.8" in e.event.detail for e in audit)


@pytest.mark.parametrize(
    "text", ["8.8.8.0/24\nnot-a-network", "8.8.8.0/24\n0.0.0.0/0", "::/0", "::/1", "::ffff:0:0/96"]
)
async def test_a_refused_list_is_shown_at_its_input_and_stores_nothing(
    site: Site, text: str
) -> None:
    """Also a network that covers a whole address family: "any source" called "restricted"."""
    await _connect(site)

    refused = await _save_networks(site, text)

    assert refused.status_code == 422
    assert 'aria-describedby="cc_allowed_networks' in refused.text
    assert "Vul netwerken in als CIDR" in refused.text
    stored = await _connections(site).get(1)
    assert stored is not None
    assert stored.allowed_networks == NetworkList()


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
    assert "proxy-instelling van de installatie" in page
    assert "platformbeheerder" in page
    # the card, for the organisation, does not name the host's configuration
    assert "FORWARDED_ALLOW_IPS" not in page


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


async def _damage_the_stored_list(site: Site, value: object) -> None:
    """Write ``value`` where the sealed record keeps the list, as a damaged record would."""
    store, name = _credentials(site), contact_center_credential_name(1)
    record = await store.get(name)
    assert record is not None
    await store.put(name, record | {"allowed_networks": value}, site.clock.now())


@pytest.mark.parametrize("damage", ["8.8.8.0/24", ["not-a-network"], ["0.0.0.0/0"], None])
async def test_a_corrupted_stored_list_refuses_every_call_and_the_card_says_so(
    site: Site, damage: object
) -> None:
    """Abuse case: a list that cannot be read is no empty list ("any source"): it is refused."""
    await _connect(site)
    await _damage_the_stored_list(site, damage)
    _set_settings(site, webhook_source_networks_contact_center=SUPPLIER_RANGE)  # not a way round

    inside = await _call_from(site, SUPPLIER)
    outside = await _call_from(site, STRANGER)
    card = (await site.client.get(PAGE)).text

    assert [(r.status_code, r.content) for r in (inside, outside)] == [(403, b"")] * 2
    assert "De eigen lijst van deze koppeling is niet te lezen" in card
    # one event per caller (ADR 0124 point 10), so one for each of the two callers
    assert await _webhook_refusals(site) == [("network", "source:wxcc,organisation:demo")] * 2


async def test_saving_a_list_repairs_a_corrupted_one(site: Site) -> None:
    await _connect(site)
    await _damage_the_stored_list(site, "damaged")

    saved = await _save_networks(site, SUPPLIER_RANGE)

    assert saved.status_code == 303
    assert (await _call_from(site, SUPPLIER)).status_code == 204


async def test_a_list_only_save_on_a_record_without_a_webhook_secret_makes_one_and_registers(
    site: Site,
) -> None:
    """``UNTOUCHED`` is for a connection that has its secret; without one the normal path runs."""
    await _connect(site)
    store, name = _credentials(site), contact_center_credential_name(1)
    record = await store.get(name)
    assert record is not None
    await store.put(
        name, {k: v for k, v in record.items() if k != "webhook_secret"}, site.clock.now()
    )
    registrar = _registrar(site)

    saved = await _save_networks(site, SUPPLIER_RANGE)

    assert "done=contact_center_registered" in saved.headers["location"]
    assert registrar.registered == [(1, "demo")]
    assert await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC) is not None


async def test_an_unreadable_default_tells_the_card_whom_to_ask_and_an_own_list_repairs_it(
    site: Site,
) -> None:
    """The organisation cannot repair the installation's default: the card says so."""
    await _connect(site)
    _set_settings(site, webhook_source_networks_contact_center="garbage")

    card = (await site.client.get(PAGE)).text
    refused = await _call_from(site, SUPPLIER)
    await _save_networks(site, SUPPLIER_RANGE)
    repaired = await _call_from(site, SUPPLIER)

    assert "De standaardlijst van de installatie is niet te lezen" in card
    assert "Vraag de platformbeheerder" in card
    assert "Advanced" not in card
    assert refused.status_code == 403
    assert repaired.status_code == 204  # an own list replaces the default, however it reads


# --- The pause (ADR 0124 point 9, step 3a) ----------------------------------------------------


class _Handler:
    """Stands in for the integration service: remembers that an event reached the handling."""

    def __init__(self) -> None:
        self.handled = 0

    async def record_task_event(self, *args: object, **kwargs: object) -> EventOutcome:
        self.handled += 1
        return EventOutcome.UNKNOWN


def _handler(site: Site) -> _Handler:
    handler = _Handler()
    site.app.state.integration_service = handler
    return handler


async def test_the_card_offers_a_button_that_pauses_and_one_that_resumes(site: Site) -> None:
    await _connect(site)

    running = (await site.client.get(PAGE)).text
    await _pause(site, "off")
    paused = (await site.client.get(PAGE)).text

    assert 'name="inbound" value="off"' in running
    assert "Bezorgingen pauzeren</button>" in running
    assert "Gepauzeerd" not in running
    assert 'name="inbound" value="on"' in paused
    assert "Bezorgingen hervatten</button>" in paused
    assert 'name="inbound" value="off"' not in paused
    assert "Gepauzeerd" in paused
    assert "Dit adres is gepauzeerd" in paused
    assert "laatste gebeurtenis" in paused  # the status screens stand still while paused
    assert "cc_inbound_enabled" not in running  # no field of the connection's form


async def test_pausing_keeps_the_secret_registers_nothing_and_is_audited_by_name(
    site: Site,
) -> None:
    await _connect(site)
    registrar = _registrar(site)
    before = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)

    paused = await _pause(site, "off")
    pressed_twice = await _pause(site, "off")  # a stale tab: the state wanted, not a toggle
    stored = await _connections(site).get(1)
    resumed = await _pause(site, "on")

    assert "done=contact_center_paused" in paused.headers["location"]
    assert "done=contact_center_paused" in pressed_twice.headers["location"]
    assert "done=contact_center_resumed" in resumed.headers["location"]
    assert stored is not None
    assert not stored.inbound_enabled
    assert registrar.registered == []
    assert await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC) == before
    audit = await site.app.state.admin_service.audit_log(SUPER)
    details = [e.event.detail for e in audit if e.event.detail.startswith("contact_center")]
    assert details[:2] == [  # newest first
        "contact_center_connection.resume",
        "contact_center_connection.pause",
    ]
    assert details.count("contact_center_connection.pause") == 1


async def test_the_pause_button_is_refused_without_the_right_or_a_state_or_a_connection(
    site: Site,
) -> None:
    await _connect(site)
    handler = _handler(site)

    await site.login_as(ORG_ADMIN)
    refused = await _pause(site, "off")
    await site.login_as(SUPER)
    no_state = await _pause(site, "toggle")
    no_connection = await _pause(site, "off", org="andere")
    stored = await _connections(site).get(1)

    assert refused.status_code == 403
    assert no_state.status_code == 422
    assert "done=contact_center_refused" in no_connection.headers["location"]
    assert stored is not None
    assert stored.inbound_enabled
    assert (await _call_from(site, SUPPLIER)).status_code == 204
    assert handler.handled == 1  # nothing was paused


async def test_a_paused_address_verifies_first_then_drops_the_event_and_remembers_nothing(
    site: Site,
) -> None:
    """A pause is no way to get an unsigned 204, and a redelivery after resuming is handled."""
    await _connect(site)
    handler = _handler(site)
    await _pause(site, "off")

    with structlog.testing.capture_logs() as logs:
        paused = await _call_from(site, SUPPLIER)
        forged = await _call_from(site, SUPPLIER, secret="x" * 43)
    await _pause(site, "on")
    resumed = await _call_from(site, SUPPLIER)

    assert (paused.status_code, paused.content) == (204, b"")
    assert (forged.status_code, forged.content) == (401, b"")  # still verified while paused
    assert resumed.status_code == 204
    assert handler.handled == 1  # only the call after resuming reached the handling
    dropped = [line for line in logs if line["event"] == "webhook.paused"]
    assert [(e["source"], e["organisation"]) for e in dropped] == [("wxcc", "demo")]
    assert set(dropped[0]) == {"event", "log_level", "source", "organisation"}
    assert await _webhook_refusals(site) == [("invalid_signature", "source:wxcc,organisation:demo")]


async def test_a_paused_address_still_refuses_what_any_address_refuses(site: Site) -> None:
    await _connect(site)
    await _save_networks(site, SUPPLIER_RANGE)
    await _pause(site, "off")
    handler = _handler(site)

    outside = await _call_from(site, STRANGER)
    inside = await _call_from(site, SUPPLIER)

    assert [(r.status_code, r.content) for r in (outside, inside)] == [(403, b""), (204, b"")]
    assert handler.handled == 0
    assert await _webhook_refusals(site) == [("network", "source:wxcc,organisation:demo")]
