"""A rotated webhook secret of an organisation keeps verifying for an overlap (ADR 0124 point 8).

Through the card's button, the sealed record and the organisation's own webhook address: the
previous secret verifies inside the overlap and not after it, a second rotation drops the secret
from before, the setting applies at once, the card says until when, and no secret reaches a log, an
audit event or the page.
"""

from datetime import timedelta

import structlog.testing

from app.adapters.db.credentials import CredentialStore
from app.adapters.db.record_names import contact_center_credential_name
from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import sealed_store
from app.domain.contact_center import ContactCenterKind
from app.domain.webhook_ledger import AddressKey, Delivered
from app.domain.webhook_secret_sweep import PreviousSecretSweep
from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import site_app
from tests.support.contact_center_site import (
    PAGE,
    SUPPLIER,
    _call_from,
    _connect,
    _connections,
    _pause,
    _registrar,
    _set_settings,
)

ROTATE = f"{PAGE}/contact-center/webhook-secret"
KEY = AddressKey("wxcc", "demo")


async def _current(site: Site) -> str:
    secret = await _connections(site).webhook_secret(1, ContactCenterKind.WEBEX_CC)
    assert secret is not None
    return secret


async def _rotate(site: Site) -> str:
    """Press the card's button; return the secret that was current before."""
    before = await _current(site)
    assert (await site.client.post(ROTATE, headers=SAME_ORIGIN)).status_code == 303
    return before


async def _status(site: Site, secret: str) -> int:
    return (await _call_from(site, SUPPLIER, secret)).status_code


def _record(site: Site) -> CredentialStore:
    state = site_app(site).state
    store = sealed_store(state.engine, state.settings)
    assert isinstance(store, CredentialStore)
    return store


async def test_the_previous_secret_keeps_verifying_for_the_overlap_and_then_stops(
    site: Site,
) -> None:
    await _connect(site)
    old = await _rotate(site)
    new = await _current(site)

    inside = (await _status(site, old), await _status(site, new))
    site.clock.advance(timedelta(minutes=59))
    still = await _status(site, old)
    site.clock.advance(timedelta(minutes=1))
    after = (await _status(site, old), await _status(site, new))

    assert inside == (204, 204)
    assert still == 204
    assert after == (401, 204)


async def test_a_delivery_under_the_previous_secret_is_accepted_like_any_other(
    site: Site,
) -> None:
    """The same outcome in the ledger and the same replay key; only one count tells them apart."""
    await _connect(site)
    old = await _rotate(site)
    new = await _current(site)

    assert await _status(site, new) == 204
    assert await _status(site, old) == 204  # the same event again, through the other secret

    view = site_app(site).state.webhook_ledger.view(KEY)
    assert view is not None
    assert view.by_previous_secret == 1
    # Both are verified deliveries of the same kind (an event about work this connection did not
    # create): whichever secret verified it, the outcome is the same.
    assert view.outcomes.get(Delivered.UNKNOWN_WORK.value) == 2


async def test_an_overlap_of_nothing_refuses_the_previous_secret_at_once(site: Site) -> None:
    """The setting is read live: set it to 0 first when the old secret leaked."""
    await _connect(site)
    old = await _rotate(site)
    assert await _status(site, old) == 204

    _set_settings(site, webhook_secret_overlap_minutes=0)

    assert await _status(site, old) == 401
    assert await _status(site, await _current(site)) == 204


async def test_a_second_rotation_inside_the_overlap_stops_the_secret_from_before(
    site: Site,
) -> None:
    await _connect(site)
    first = await _rotate(site)
    second = await _rotate(site)
    third = await _current(site)

    assert await _status(site, first) == 401
    assert await _status(site, second) == 204
    assert await _status(site, third) == 204
    record = await _record(site).get(contact_center_credential_name(1))
    assert record is not None
    assert record["previous_webhook_secret"] == second


async def test_the_previous_secret_is_sealed_in_the_record_and_never_shown(site: Site) -> None:
    await _connect(site)
    old = await _rotate(site)
    new = await _current(site)

    record = await _record(site).get(contact_center_credential_name(1))
    page = (await site.client.get(PAGE)).text

    assert record is not None
    assert (record["previous_webhook_secret"], record["webhook_secret"]) == (old, new)
    assert old not in page
    assert new not in page


async def test_the_card_says_until_when_the_previous_secret_works_and_what_the_button_does(
    site: Site,
) -> None:
    await _connect(site)
    before = (await site.client.get(PAGE)).text
    await _rotate(site)

    after = (await site.client.get(PAGE)).text
    site.clock.advance(timedelta(minutes=61))
    later = (await site.client.get(PAGE)).text

    assert "Het vorige geheim werkt nog tot" not in before
    assert "blijft nog 60 minuten werken" in before  # the button says what will happen
    assert "Het vorige geheim werkt nog tot" in after
    assert "09:00" in after  # NOW is 09:00 Amsterdam; 60 minutes on, in the organisation's zone
    assert "Maakt u nu nog een nieuw geheim" in after  # warns before a second rotation
    assert "Het vorige geheim werkt nog tot" not in later
    assert "Maakt u nu nog een nieuw geheim" not in later


async def test_the_button_says_so_when_the_overlap_is_nothing(site: Site) -> None:
    await _connect(site)
    _set_settings(site, webhook_secret_overlap_minutes=0)

    page = (await site.client.get(PAGE)).text
    await _rotate(site)
    rotated = (await site.client.get(PAGE)).text

    assert "werkt direct niet meer" in page
    assert "blijft nog" not in page
    assert "Het vorige geheim werkt nog tot" not in rotated


async def test_the_card_counts_what_the_previous_secret_verified(site: Site) -> None:
    await _connect(site)
    old = await _rotate(site)
    await _status(site, old)

    page = (await site.client.get(PAGE)).text

    assert "Waarvan gecontroleerd met het vorige geheim" in page


async def test_a_write_removes_a_previous_secret_that_no_setting_could_keep(site: Site) -> None:
    await _connect(site)
    await _rotate(site)
    site.clock.advance(timedelta(hours=25))
    await site.login_as(SUPER)  # the session ended long ago

    await _pause(site, "off")  # the next write of the record

    record = await _record(site).get(contact_center_credential_name(1))
    assert record is not None
    assert "previous_webhook_secret" not in record


async def test_the_sweep_removes_a_previous_secret_once_the_live_overlap_has_ended(
    site: Site,
) -> None:
    await _connect(site)
    await _rotate(site)
    state = site_app(site).state
    sweep = PreviousSecretSweep(
        SqlUnitOfWork(state.engine),
        site.clock,
        _connections(site),
        lambda: timedelta(minutes=state.settings.webhook_secret_overlap_minutes),
    )

    inside = await sweep.run()
    site.clock.advance(timedelta(minutes=60))
    ended = await sweep.run()
    again = await sweep.run()

    assert (inside, ended, again) == (0, 1, 0)
    stored = await _connections(site).get(1)
    assert stored is not None
    assert not stored.has_previous_webhook_secret


async def test_moving_to_another_tenant_keeps_no_previous_secret(site: Site) -> None:
    """An overlap is for a rotation; a new host or client must stop the old subscription."""
    await _connect(site)
    old = await _current(site)
    from tests.support.contact_center_site import FORM

    moved = await site.client.post(
        f"{PAGE}/contact-center",
        data=FORM
        | {"cc_client_id": "Another-client", "cc_client_secret": "", "cc_refresh_token": ""},
        headers=SAME_ORIGIN,
    )

    assert moved.status_code == 303
    assert await _status(site, old) == 401
    stored = await _connections(site).get(1)
    assert stored is not None
    assert not stored.has_previous_webhook_secret


async def test_no_secret_reaches_a_log_an_audit_event_or_the_security_events(site: Site) -> None:
    await _connect(site)
    _registrar(site)
    with structlog.testing.capture_logs() as logs:
        old = await _rotate(site)
        new = await _current(site)
        await _status(site, old)  # verified under the previous secret
        site.clock.advance(timedelta(minutes=61))
        await _status(site, old)  # refused after it ended
        await _status(site, "x" * 43)
    audit = await site_app(site).state.admin_service.audit_log(SUPER)

    texts = repr(logs) + repr([(e.event.detail, e.event.reason) for e in audit])
    assert old not in texts
    assert new not in texts
    assert "previous_webhook_secret" not in texts
    assert any(e.event.detail.endswith("rotate_webhook_secret") for e in audit)
