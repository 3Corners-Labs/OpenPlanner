"""An organisation's own contact-center connection (ADR 0032 points 9–11)."""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.contact_center import ConnectionChange, ContactCenterKind, Registration
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeContactCenterConnections, FakeOrganisationDispatchers
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
def connections() -> FakeContactCenterConnections:
    return FakeContactCenterConnections()


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def service(
    uow: InMemoryUnitOfWork, clock: FixedClock, connections: FakeContactCenterConnections
) -> ContactCenterConnectionService:
    return ContactCenterConnectionService(
        uow, clock, AdminService(uow, clock), connections, lambda: None
    )


async def test_a_connection_is_kept_and_its_audit_names_fields_not_values(
    service: ContactCenterConnectionService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(SUPER, "demo", change(api_base_url=f"{HOST}/"))

    shown = await service.view(SUPER, "demo")

    assert shown is not None
    assert (shown.kind, shown.api_base_url, shown.client_id) == (WXCC, HOST, "C0ffee-client")
    assert shown.has_client_secret
    assert shown.has_refresh_token
    [event] = [e for e in uow.state.audit if "contact_center_connection" in e.detail]
    assert event.detail == (
        "contact_center_connection.save,kind:webex_cc,client_secret,refresh_token"
    )
    assert "client-secret-value" not in event.detail


async def test_a_new_connection_needs_a_client_secret(
    service: ContactCenterConnectionService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.save(SUPER, "demo", change(client_secret=None))
    assert refused.value.field == "contact_center_connection.client_secret"

    await service.save(SUPER, "demo", change())
    await service.save(SUPER, "demo", change(client_secret=None, refresh_token=None))

    assert uow.state.audit[-1].detail == "contact_center_connection.save,kind:webex_cc"


async def test_rotating_makes_a_new_webhook_secret(
    service: ContactCenterConnectionService,
    connections: FakeContactCenterConnections,
    clock: FixedClock,
) -> None:
    await service.save(SUPER, "demo", change())
    clock.advance(timedelta(days=1))

    await service.rotate_webhook_secret(SUPER, "demo")

    assert connections.rotations == 1
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
    service: ContactCenterConnectionService, connections: FakeContactCenterConnections
) -> None:
    await service.save(SUPER, "demo", change())

    await service.remove(SUPER, "demo")

    assert await service.view(SUPER, "demo") is None
    assert connections.connections == {}


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
    service = ContactCenterConnectionService(
        uow, clock, AdminService(uow, clock), None, lambda: None
    )

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
        ("api_base_url", "https://user@api.example.com"),
        ("api_base_url", "https://localhost"),
        ("api_base_url", "https://api.localhost:8443"),
        ("api_base_url", "https://127.0.0.1"),
        ("api_base_url", "https://10.0.0.5"),
        ("api_base_url", "https://192.168.1.1:443"),
        ("api_base_url", "https://169.254.169.254"),
        ("api_base_url", "https://127.1"),
        ("api_base_url", "https://2130706433"),
        ("api_base_url", "https://0x7f000001"),
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


async def test_saving_and_rotating_register_the_address_and_audit_the_outcome(
    uow: InMemoryUnitOfWork, clock: FixedClock, connections: FakeContactCenterConnections
) -> None:
    """ADR 0032 CC-4d: the platform is told at once; its answer is audited, never undone."""
    registrar = FakeOrganisationDispatchers(outcome=Registration.REFUSED)
    service = ContactCenterConnectionService(
        uow, clock, AdminService(uow, clock), connections, lambda: registrar
    )

    saved = await service.save(SUPER, "demo", change())
    rotated = await service.rotate_webhook_secret(SUPER, "demo")

    assert (saved, rotated) == (Registration.REFUSED, Registration.REFUSED)
    assert registrar.registered == [(1, "demo"), (1, "demo")]
    details = [e.detail for e in uow.state.audit if e.detail.endswith("outcome:refused")]
    assert details == ["contact_center_connection.register,outcome:refused"] * 2
    assert await service.view(SUPER, "demo") is not None  # the change itself stays


async def test_an_organisations_webhook_address_exists_with_its_connection(
    service: ContactCenterConnectionService,
) -> None:
    await service.save(SUPER, "demo", change())

    target = await service.webhook_target("demo", WXCC)

    assert target.found
    assert (target.organisation_id, target.secret, target.organisation) == (
        1,
        "secret-of-1",  # gitleaks:allow (test value)
        "demo",
    )


async def test_a_webhook_address_without_a_connection_names_an_organisation_that_exists(
    service: ContactCenterConnectionService,
) -> None:
    target = await service.webhook_target("andere", WXCC)

    assert not target.found
    assert target.organisation == "andere"


async def test_a_slug_of_an_organisation_that_does_not_exist_is_never_returned(
    service: ContactCenterConnectionService,
) -> None:
    for slug in ("nobody", "Not-A-Slug", "", "x" * 200, "demo/../andere"):
        target = await service.webhook_target(slug, WXCC)
        assert not target.found, slug
        assert target.organisation is None, slug


async def test_without_a_settings_key_no_webhook_address_exists(
    uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    service = ContactCenterConnectionService(
        uow, clock, AdminService(uow, clock), None, lambda: None
    )

    target = await service.webhook_target("demo", WXCC)

    assert (target.found, target.organisation) == (False, None)


async def test_without_a_registrar_nothing_is_registered(
    service: ContactCenterConnectionService,
) -> None:
    assert await service.save(SUPER, "demo", change()) is Registration.NOT_READY
