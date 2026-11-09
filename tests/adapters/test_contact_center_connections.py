"""The organisations' contact-center connections sealed in one record each (ADR 0032 point 9)."""

from datetime import timedelta

from app.adapters.contact_center_connections import SealedConnections
from app.domain.contact_center import ConnectionChange, ContactCenterKind
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
        1, ConnectionChange(WXCC, "https://api.wxcc-eu2.cisco.com", "other"), NOW
    )

    record = secrets.values["contact-center:1"]
    assert record["client_secret"] == "client-secret"
    assert record["client_id"] == "other"
    assert record["webhook_secret"] == webhook


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
