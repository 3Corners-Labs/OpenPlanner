"""The secret of the address for message deliveries, in the sealed record (ADR 0124 point 15)."""

from datetime import timedelta

from app.adapters.contact_center_connections import (
    MESSAGE_KEY,
    MESSAGE_PREVIOUS_KEY,
    SealedConnections,
)
from app.adapters.db.record_names import contact_center_credential_name
from app.domain.contact_center import ConnectionChange, ContactCenterKind
from tests.fakes.integrations import FakeSecrets
from tests.support.domain import NOW

WXCC = ContactCenterKind.WEBEX_CC
OWN = ConnectionChange(WXCC, "https://api.wxcc-eu1.cisco.com", "client", "secret", "refresh")
NAME = contact_center_credential_name(1)
OVERLAP = timedelta(minutes=60)


async def _connected() -> tuple[SealedConnections, FakeSecrets]:
    store = FakeSecrets()
    connections = SealedConnections(store)
    await connections.save(1, OWN, NOW)
    return connections, store


async def test_the_address_has_no_secret_until_one_is_made_and_the_events_secret_is_not_it() -> (
    None
):
    connections, _ = await _connected()

    assert await connections.message_secrets(1, WXCC) is None
    shown = await connections.get(1)
    assert shown is not None
    assert (shown.message_secret_at, shown.has_previous_message_secret) == (None, False)


async def test_making_a_secret_returns_it_once_and_the_card_never_sees_it() -> None:
    connections, store = await _connected()

    made = await connections.make_message_secret(1, NOW)
    held = await connections.message_secrets(1, WXCC)
    shown = await connections.get(1)

    assert made is not None
    assert held is not None
    assert (held.current, held.previous, held.rotated_at) == (made, None, NOW)
    assert shown is not None
    assert shown.message_secret_at == NOW
    assert made not in repr(shown)
    assert made != (await connections.webhook_secret(1, WXCC))  # its own, never the events secret
    assert store.values[contact_center_credential_name(1)][MESSAGE_KEY] == made


async def test_a_new_secret_keeps_the_one_before_as_the_previous_one_for_the_overlap() -> None:
    connections, store = await _connected()
    first = await connections.make_message_secret(1, NOW)

    second = await connections.make_message_secret(1, NOW + timedelta(minutes=5))
    third = await connections.make_message_secret(1, NOW + timedelta(minutes=10))
    held = await connections.message_secrets(1, WXCC)

    assert held is not None
    assert (held.current, held.previous) == (third, second)  # the one from before is gone
    assert first not in (held.current, held.previous)
    shown = await connections.get(1)
    assert shown is not None
    assert shown.has_previous_message_secret
    assert store.values[contact_center_credential_name(1)][MESSAGE_PREVIOUS_KEY] == second


async def test_without_a_connection_nothing_is_made() -> None:
    connections = SealedConnections(FakeSecrets())
    assert await connections.make_message_secret(1, NOW) is None


async def test_the_sweep_drops_an_ended_previous_message_secret_and_keeps_a_running_one() -> None:
    connections, _ = await _connected()
    await connections.make_message_secret(1, NOW)
    await connections.make_message_secret(1, NOW + timedelta(minutes=5))

    running = await connections.drop_expired_previous([1], NOW + timedelta(minutes=30), OVERLAP)
    ended = await connections.drop_expired_previous([1], NOW + timedelta(minutes=70), OVERLAP)
    held = await connections.message_secrets(1, WXCC)

    assert (running, ended) == (0, 1)
    assert held is not None
    assert held.previous is None


async def test_a_write_of_the_record_tidies_a_previous_message_secret_nothing_could_keep() -> None:
    connections, store = await _connected()
    await connections.make_message_secret(1, NOW)
    await connections.make_message_secret(1, NOW + timedelta(minutes=5))

    await connections.save(1, OWN, NOW + timedelta(days=2))  # past the longest overlap

    assert MESSAGE_PREVIOUS_KEY not in store.values[contact_center_credential_name(1)]
