"""The sealed record keeps the previous webhook secret of a rotation (ADR 0124 point 8)."""

from datetime import timedelta

import httpx
import pytest

from app.adapters.contact_center_connections import PREVIOUS_KEY, SealedConnections
from app.adapters.db.record_names import contact_center_credential_name
from app.adapters.organisation_dispatch import OrganisationWxcc
from app.adapters.wxcc import DESCRIPTOR
from app.domain.contact_center import (
    ConnectionChange,
    ContactCenterKind,
    Registration,
    WebhookSecretUpdate,
)
from app.domain.webhook_secrets import OVERLAP_MAX
from tests.fakes.clock import FixedClock
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


async def test_a_rotation_keeps_the_secret_that_was_current_as_the_previous_one() -> None:
    connections, store = await _connected()
    first = await connections.webhook_secrets(1, WXCC)
    assert first is not None
    assert first.previous is None

    await connections.rotate_webhook_secret(1, NOW + timedelta(minutes=5))

    second = await connections.webhook_secrets(1, WXCC)
    assert second is not None
    assert second.previous == first.current
    assert second.current != first.current
    assert second.rotated_at == NOW + timedelta(minutes=5)
    assert store.values[NAME][PREVIOUS_KEY] == first.current
    shown = await connections.get(1)
    assert shown is not None
    assert shown.has_previous_webhook_secret


async def test_a_second_rotation_drops_the_secret_from_before() -> None:
    connections, _ = await _connected()
    first = await connections.webhook_secret(1, WXCC)
    await connections.rotate_webhook_secret(1, NOW + timedelta(minutes=5))
    second = await connections.webhook_secret(1, WXCC)

    await connections.rotate_webhook_secret(1, NOW + timedelta(minutes=10))

    held = await connections.webhook_secrets(1, WXCC)
    assert held is not None
    assert held.previous == second
    assert first not in (held.current, held.previous)


async def test_a_record_without_a_previous_secret_reads_as_before() -> None:
    connections, store = await _connected()
    store.values[NAME].pop(PREVIOUS_KEY, None)

    shown = await connections.get(1)
    held = await connections.webhook_secrets(1, WXCC)

    assert shown is not None
    assert not shown.has_previous_webhook_secret
    assert held is not None
    assert held.previous is None


async def test_the_secrets_of_another_platform_or_without_a_connection_are_none() -> None:
    connections, store = await _connected()

    assert await connections.webhook_secrets(2, WXCC) is None
    store.values[NAME]["kind"] = "someone_else"
    assert await connections.webhook_secrets(1, WXCC) is None


async def test_a_save_that_moves_to_another_tenant_keeps_no_previous_secret() -> None:
    connections, store = await _connected()
    await connections.rotate_webhook_secret(1, NOW)
    assert PREVIOUS_KEY in store.values[NAME]

    await connections.save(1, ConnectionChange(WXCC, OWN.api_base_url, "another-client"), NOW)

    assert PREVIOUS_KEY not in store.values[NAME]


async def test_a_save_that_keeps_the_tenant_keeps_the_previous_secret() -> None:
    connections, store = await _connected()
    await connections.rotate_webhook_secret(1, NOW)

    await connections.save(1, ConnectionChange(WXCC, OWN.api_base_url, "client"), NOW)

    assert PREVIOUS_KEY in store.values[NAME]


async def test_a_write_removes_a_previous_secret_only_when_no_setting_could_keep_it() -> None:
    connections, store = await _connected()
    await connections.rotate_webhook_secret(1, NOW)

    await connections.set_inbound_enabled(1, False, NOW + OVERLAP_MAX - timedelta(seconds=1))
    assert PREVIOUS_KEY in store.values[NAME]
    await connections.set_inbound_enabled(1, True, NOW + OVERLAP_MAX)

    assert PREVIOUS_KEY not in store.values[NAME]


async def test_the_sweep_removes_only_the_records_whose_live_overlap_has_ended() -> None:
    connections, store = await _connected()
    await connections.save(2, OWN, NOW)
    await connections.rotate_webhook_secret(1, NOW)
    await connections.rotate_webhook_secret(2, NOW + timedelta(minutes=30))
    later = NOW + OVERLAP

    removed = await connections.drop_expired_previous([1, 2, 3], later, OVERLAP)

    assert removed == 1
    assert PREVIOUS_KEY not in store.values[NAME]
    assert PREVIOUS_KEY in store.values[contact_center_credential_name(2)]
    assert await connections.drop_expired_previous([1, 2], later, OVERLAP) == 0
    assert await connections.drop_expired_previous([], later, OVERLAP) == 0


# --- what the adapter declares and does for it --------------------------------------------------


def test_the_contact_center_adapter_declares_how_its_subscription_secret_changes() -> None:
    assert DESCRIPTOR.capabilities.webhook_secret_update is WebhookSecretUpdate.RENEW


async def test_a_capability_the_adapter_has_no_way_for_is_refused_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeSecrets()
    connections = SealedConnections(store)
    await connections.save(1, OWN, NOW)
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

    organisations = OrganisationWxcc(
        connections,
        FakeSecrets(),
        httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        FixedClock(NOW),
        "https://webexapis.com/v1/access_token",
        webhook_base="https://openplanner.example",
    )
    monkeypatch.setattr("app.adapters.organisation_dispatch._SECRET_UPDATES", {})

    assert await organisations.register(1, "demo") is Registration.REFUSED
    assert not [r for r in seen if "subscriptions" in r.url.path]
