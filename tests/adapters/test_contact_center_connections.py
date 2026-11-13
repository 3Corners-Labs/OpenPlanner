"""The organisations' contact-center connections sealed in one record each (ADR 0032 point 9)."""

from datetime import timedelta

import pytest

from app.adapters.contact_center_connections import SealedConnections
from app.domain.contact_center import ConnectionChange, ContactCenterKind
from app.domain.networks import NetworkList
from tests.fakes.integrations import FakeSecrets
from tests.support.domain import NOW

WXCC = ContactCenterKind.WEBEX_CC
CHANGE = ConnectionChange(
    WXCC, "https://api.wxcc-eu1.cisco.com", "client", "client-secret", "refresh-token"
)


async def test_one_record_holds_the_connection_and_a_generated_webhook_secret() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)

    await connections.save(1, CHANGE, NOW)

    record = secrets.values["contact-center:1"]
    assert record["client_secret"] == "client-secret"
    assert record["refresh_token"] == "refresh-token"
    assert len(str(record["webhook_secret"])) >= 32  # WxCC wants 32–256 characters
    shown = await connections.get(1)
    assert shown is not None
    assert shown.webhook_secret_at == NOW
    assert "client-secret" not in repr(shown)


async def test_a_blank_secret_keeps_the_stored_one_and_the_webhook_secret_stays() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    await connections.save(1, CHANGE, NOW)
    webhook = secrets.values["contact-center:1"]["webhook_secret"]

    await connections.save(
        1, ConnectionChange(WXCC, "https://api.wxcc-eu1.cisco.com", "client"), NOW
    )

    record = secrets.values["contact-center:1"]
    assert record["client_secret"] == "client-secret"
    assert record["refresh_token"] == "refresh-token"
    assert record["webhook_secret"] == webhook  # same tenant: the address keeps its secret


async def test_rotating_replaces_the_webhook_secret_and_does_nothing_without_a_connection() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    await connections.rotate_webhook_secret(1, NOW)
    assert secrets.values == {}
    await connections.save(1, CHANGE, NOW)
    before = secrets.values["contact-center:1"]["webhook_secret"]

    await connections.rotate_webhook_secret(1, NOW + timedelta(days=1))

    assert secrets.values["contact-center:1"]["webhook_secret"] != before
    shown = await connections.get(1)
    assert shown is not None
    assert shown.webhook_secret_at == NOW + timedelta(days=1)


async def test_the_webhook_secret_is_only_for_the_connected_kind_and_organisation() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    assert await connections.webhook_secret(1, WXCC) is None
    await connections.save(1, CHANGE, NOW)

    assert (
        await connections.webhook_secret(1, WXCC)
        == secrets.values["contact-center:1"]["webhook_secret"]
    )
    assert await connections.webhook_secret(2, WXCC) is None
    secrets.values["contact-center-tokens:1"] = {"access_token": "issued"}
    await connections.delete(1)
    assert await connections.webhook_secret(1, WXCC) is None
    assert secrets.values == {}  # its access tokens go with it


async def test_another_tenant_gets_a_new_webhook_secret() -> None:
    """A changed host or client: the previous tenant's subscription must stop verifying."""
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    await connections.save(1, CHANGE, NOW)
    before = await connections.webhook_secret(1, WXCC)

    await connections.save(
        1, ConnectionChange(WXCC, "https://api.wxcc-us1.cisco.com", "client"), NOW
    )

    assert await connections.webhook_secret(1, WXCC) != before


async def test_a_record_made_before_the_allowed_networks_reads_as_an_empty_list() -> None:
    """ADR 0124: no migration; an existing sealed record keeps working without the field."""
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    await connections.save(1, CHANGE, NOW)
    assert "allowed_networks" not in secrets.values["contact-center:1"]

    shown = await connections.get(1)

    assert shown is not None
    assert shown.allowed_networks == NetworkList()


async def test_the_allowed_networks_live_in_the_sealed_record_beside_the_other_fields() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    networks = ("8.8.8.0/24", "2001:4860:4860::/48")
    change = ConnectionChange(WXCC, CHANGE.api_base_url, "client", "s", "r", "\n".join(networks))

    await connections.save(1, change, NOW)

    assert secrets.values["contact-center:1"]["allowed_networks"] == list(networks)
    shown = await connections.get(1)
    assert shown is not None
    assert shown.allowed_networks == NetworkList(networks)


async def test_saving_the_networks_alone_keeps_the_webhook_secret_and_every_other_field() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    await connections.save(1, CHANGE, NOW)
    before = dict(secrets.values["contact-center:1"])
    only = ConnectionChange(WXCC, CHANGE.api_base_url, "client", allowed_networks="8.8.8.0/24")

    await connections.save(1, only, NOW + timedelta(days=1))

    assert secrets.values["contact-center:1"] == before | {"allowed_networks": ["8.8.8.0/24"]}
    assert await connections.webhook_secret(1, WXCC) == before["webhook_secret"]


async def test_a_save_without_the_field_keeps_the_list_and_an_empty_one_clears_it() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    listed = ConnectionChange(WXCC, CHANGE.api_base_url, "client", allowed_networks="8.8.8.0/24")
    await connections.save(1, CHANGE, NOW)
    await connections.save(1, listed, NOW)

    await connections.save(1, ConnectionChange(WXCC, CHANGE.api_base_url, "client"), NOW)
    kept = await connections.get(1)
    cleared = ConnectionChange(WXCC, CHANGE.api_base_url, "client", allowed_networks="")
    await connections.save(1, cleared, NOW)
    emptied = await connections.get(1)

    assert kept is not None
    assert kept.allowed_networks == NetworkList(("8.8.8.0/24",))
    assert emptied is not None
    assert emptied.allowed_networks == NetworkList()


@pytest.mark.parametrize(
    "stored",
    [
        ["not-a-network"],
        [""],
        ["8.8.8.9/24"],
        ["0.0.0.0/0"],
        ["8.8.8.0/24", 7],
        "8.8.8.0/24",
        {"a": 1},
        None,
        7,
    ],
)
async def test_a_stored_list_that_cannot_be_read_is_unreadable_never_empty(stored: object) -> None:
    """A hand-edited or damaged record must not read as "any source": the gate refuses it."""
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    await connections.save(1, CHANGE, NOW)
    secrets.values["contact-center:1"]["allowed_networks"] = stored

    shown = await connections.get(1)

    assert shown is not None
    assert shown.allowed_networks == NetworkList(unreadable=True)
    assert shown.allowed_networks != NetworkList()


async def test_saving_a_list_replaces_an_unreadable_one() -> None:
    secrets = FakeSecrets()
    connections = SealedConnections(secrets)
    await connections.save(1, CHANGE, NOW)
    secrets.values["contact-center:1"]["allowed_networks"] = "damaged"

    await connections.save(
        1, ConnectionChange(WXCC, CHANGE.api_base_url, "client", allowed_networks="8.8.8.0/24"), NOW
    )

    shown = await connections.get(1)
    assert shown is not None
    assert shown.allowed_networks == NetworkList(("8.8.8.0/24",))
