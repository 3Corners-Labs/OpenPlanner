"""An organisation's own contact-center connection (ADR 0032 points 9–11)."""

from datetime import timedelta

import pytest

from app.adapters.contact_center_connections import SealedConnections
from app.domain.admin import AdminService
from app.domain.contact_center import ConnectionChange, ContactCenterKind
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeSecrets
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW

WXCC = ContactCenterKind.WEBEX_CC
HOST = "https://api.wxcc-eu1.cisco.com"


def change(**overrides: object) -> ConnectionChange:
    values: dict[str, object] = {
        "kind": WXCC,
        "api_base_url": HOST,
        "client_id": "C0ffee-client",
        "client_secret": "client-secret-value",
        "refresh_token": "refresh-token-value",
    }
    values.update(overrides)
    return ConnectionChange(**values)  # type: ignore[arg-type]


@pytest.fixture
def secrets() -> FakeSecrets:
    return FakeSecrets()


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(
    uow: InMemoryUnitOfWork, clock: FixedClock, secrets: FakeSecrets
) -> ContactCenterConnectionService:
    return ContactCenterConnectionService(
        uow, clock, AdminService(uow, clock), SealedConnections(secrets)
    )


async def test_a_connection_is_kept_sealed_and_shown_without_its_secrets(
    service: ContactCenterConnectionService, uow: InMemoryUnitOfWork, secrets: FakeSecrets
) -> None:
    await service.save(SUPER, "demo", change())

    shown = await service.view(SUPER, "demo")

    assert shown is not None
    assert (shown.kind, shown.api_base_url, shown.client_id) == (WXCC, HOST, "C0ffee-client")
    assert shown.has_client_secret
    assert shown.has_refresh_token
    assert shown.webhook_secret_at == NOW
    assert "client-secret-value" not in repr(shown)
    stored = secrets.values["contact-center:1"]
    assert len(str(stored["webhook_secret"])) >= 32  # WxCC wants 32–256 characters
    [event] = [e for e in uow.state.audit if "contact_center_connection" in e.detail]
    assert event.detail == (
        "contact_center_connection.save,kind:webex_cc,client_secret,refresh_token"
    )
    assert "client-secret-value" not in event.detail


async def test_a_blank_secret_keeps_the_stored_one_but_a_new_connection_needs_one(
    service: ContactCenterConnectionService, secrets: FakeSecrets
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.save(SUPER, "demo", change(client_secret=None))
    assert refused.value.field == "contact_center_connection.client_secret"

    await service.save(SUPER, "demo", change())
    await service.save(
        SUPER, "demo", change(client_secret=None, refresh_token=None, client_id="other")
    )

    stored = secrets.values["contact-center:1"]
    assert stored["client_secret"] == "client-secret-value"
    assert stored["client_id"] == "other"


async def test_rotating_replaces_the_webhook_secret(
    service: ContactCenterConnectionService, secrets: FakeSecrets, clock: FixedClock
) -> None:
    await service.save(SUPER, "demo", change())
    before = secrets.values["contact-center:1"]["webhook_secret"]
    clock.advance(timedelta(days=1))

    await service.rotate_webhook_secret(SUPER, "demo")

    assert secrets.values["contact-center:1"]["webhook_secret"] != before
    shown = await service.view(SUPER, "demo")
    assert shown is not None
    assert shown.webhook_secret_at == NOW + timedelta(days=1)


async def test_rotating_without_a_connection_is_refused(
    service: ContactCenterConnectionService,
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.rotate_webhook_secret(SUPER, "demo")

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_removing_falls_back_to_the_installation(
    service: ContactCenterConnectionService, secrets: FakeSecrets
) -> None:
    await service.save(SUPER, "demo", change())

    await service.remove(SUPER, "demo")

    assert await service.view(SUPER, "demo") is None
    assert secrets.values == {}


@pytest.mark.parametrize("who", [ORG_ADMIN, OUTSIDER, GROUP_ADMIN])
async def test_only_the_superuser_may_touch_it(
    service: ContactCenterConnectionService, who: object
) -> None:
    for call in (
        service.view(who, "demo"),  # type: ignore[arg-type]
        service.save(who, "demo", change()),  # type: ignore[arg-type]
        service.rotate_webhook_secret(who, "demo"),  # type: ignore[arg-type]
        service.remove(who, "demo"),  # type: ignore[arg-type]
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_without_a_settings_key_nothing_can_be_kept(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = ContactCenterConnectionService(uow, clock, AdminService(uow, clock), None)

    assert not service.available
    assert await service.view(SUPER, "demo") is None
    with pytest.raises(DomainError) as refused:
        await service.save(SUPER, "demo", change())
    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_base_url", "http://api.example.com"),
        ("api_base_url", "https://api.example.com/path?x=1"),
        ("client_id", "has space"),
        ("client_secret", "two words"),
        ("refresh_token", "x" * 4097),
    ],
)
def test_the_fields_are_checked_and_the_refusal_names_only_the_field(
    field: str, value: str
) -> None:
    with pytest.raises(DomainError) as refused:
        change(**{field: value})

    assert refused.value.field == f"contact_center_connection.{field}"


async def test_the_webhook_secret_is_only_for_the_connected_kind(
    service: ContactCenterConnectionService, secrets: FakeSecrets
) -> None:
    connections = SealedConnections(secrets)
    assert await connections.webhook_secret(1, WXCC) is None
    await service.save(SUPER, "demo", change())

    assert (
        await connections.webhook_secret(1, WXCC)
        == secrets.values["contact-center:1"]["webhook_secret"]
    )
    assert await connections.webhook_secret(2, WXCC) is None
    assert await service.organisation_of("demo") == 1
    assert await service.organisation_of("unknown") is None
