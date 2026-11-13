"""An organisation's own contact-center connection (ADR 0032 points 9–11)."""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.contact_center import ConnectionChange, ContactCenterKind, Registration
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.errors import DomainError, ErrorCode
from app.domain.networks import NetworkList
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
def registrar() -> FakeOrganisationDispatchers:
    return FakeOrganisationDispatchers()


@pytest.fixture
def registering(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    connections: FakeContactCenterConnections,
    registrar: FakeOrganisationDispatchers,
) -> ContactCenterConnectionService:
    """A service whose registrar answers ``registered`` and remembers every call."""
    return ContactCenterConnectionService(
        uow, clock, AdminService(uow, clock), connections, lambda: registrar
    )


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


SUPPLIER_NETWORKS = ("8.8.8.0/24", "2001:4860:4860::/48")
SUPPLIER_TEXT = "\n".join(SUPPLIER_NETWORKS)
SUPPLIED = NetworkList(SUPPLIER_NETWORKS)


async def test_the_allowed_networks_are_kept_canonical_and_audited_by_name_only(
    service: ContactCenterConnectionService, uow: InMemoryUnitOfWork
) -> None:
    """ADR 0124 point 3: the field name is audited, the networks are not."""
    await service.save(SUPER, "demo", change(allowed_networks="8.8.8.8/24, 8.8.8.0/24"))

    shown = await service.view(SUPER, "demo")

    assert shown is not None
    assert shown.allowed_networks == NetworkList(("8.8.8.0/24",))
    assert uow.state.audit[-1].detail == (
        "contact_center_connection.save,kind:webex_cc,client_secret,refresh_token,allowed_networks"
    )
    assert "8.8.8" not in " ".join(e.detail for e in uow.state.audit)


async def test_a_save_without_the_field_keeps_the_list_and_an_empty_one_clears_it(
    service: ContactCenterConnectionService, uow: InMemoryUnitOfWork
) -> None:
    await service.save(SUPER, "demo", change(allowed_networks=SUPPLIER_TEXT))

    await service.save(SUPER, "demo", change(client_secret=None, refresh_token=None))
    kept = await service.view(SUPER, "demo")
    await service.save(
        SUPER, "demo", change(client_secret=None, refresh_token=None, allowed_networks="")
    )
    cleared = await service.view(SUPER, "demo")

    assert kept is not None
    assert kept.allowed_networks == SUPPLIED
    assert cleared is not None
    assert cleared.allowed_networks == NetworkList()
    assert uow.state.audit[-1].detail == (
        "contact_center_connection.save,kind:webex_cc,allowed_networks"
    )


@pytest.mark.parametrize(
    "networks",
    [
        "nonsense",
        "8.8.8.0/33",
        ",".join(f"10.0.{n}.0/24" for n in range(33)),
        "8.8.8.0/24, 0.0.0.0/0",
        "::/0",
    ],
)
async def test_a_list_that_is_no_list_of_at_most_32_networks_or_covers_a_family_is_refused(
    service: ContactCenterConnectionService, uow: InMemoryUnitOfWork, networks: str
) -> None:
    with pytest.raises(DomainError) as refused:
        await service.save(SUPER, "demo", change(allowed_networks=networks))

    assert refused.value.field == "contact_center_connection.allowed_networks"
    assert await service.view(SUPER, "demo") is None  # nothing stored
    assert not [e for e in uow.state.audit if "contact_center_connection" in e.detail]  # no audit


async def test_changing_the_networks_alone_neither_makes_a_new_secret_nor_registers_again(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    connections: FakeContactCenterConnections,
    registrar: FakeOrganisationDispatchers,
    registering: ContactCenterConnectionService,
) -> None:
    """The subscription depends on the secret, the host and the client, not on who may call."""
    await registering.save(SUPER, "demo", change())
    before = await registering.view(SUPER, "demo")
    clock.advance(timedelta(days=1))

    outcome = await registering.save(
        SUPER,
        "demo",
        change(client_secret=None, refresh_token=None, allowed_networks=SUPPLIER_TEXT),
    )

    after = await registering.view(SUPER, "demo")
    assert outcome is Registration.UNTOUCHED
    assert registrar.registered == [(1, "demo")]  # the first save only
    assert connections.rotations == 0
    assert before is not None
    assert after is not None
    assert after.webhook_secret_at == before.webhook_secret_at
    assert not [e for e in uow.state.audit if "register" in e.detail][1:]


async def test_a_record_without_a_webhook_secret_takes_the_normal_path_for_a_list_only_save(
    connections: FakeContactCenterConnections,
    registrar: FakeOrganisationDispatchers,
    registering: ContactCenterConnectionService,
) -> None:
    """``UNTOUCHED`` is only for a connection that has its secret: without one it is made."""
    await registering.save(SUPER, "demo", change())
    connections.secretless.add(1)

    outcome = await registering.save(
        SUPER,
        "demo",
        change(client_secret=None, refresh_token=None, allowed_networks=SUPPLIER_TEXT),
    )

    assert outcome is Registration.REGISTERED
    assert registrar.registered == [(1, "demo")] * 2


async def test_an_unreadable_stored_list_is_replaced_by_saving_a_list_or_an_empty_one(
    connections: FakeContactCenterConnections, registering: ContactCenterConnectionService
) -> None:
    await registering.save(SUPER, "demo", change())
    connections.unreadable.add(1)
    shown = await registering.view(SUPER, "demo")
    assert shown is not None
    assert shown.allowed_networks == NetworkList(unreadable=True)

    outcome = await registering.save(
        SUPER, "demo", change(client_secret=None, refresh_token=None, allowed_networks="")
    )

    after = await registering.view(SUPER, "demo")
    assert outcome is Registration.UNTOUCHED  # it differed from the unreadable list: a change
    assert after is not None
    assert after.allowed_networks == NetworkList()


async def test_the_networks_with_another_field_still_register_again(
    registrar: FakeOrganisationDispatchers, registering: ContactCenterConnectionService
) -> None:
    await registering.save(SUPER, "demo", change())

    new_secret = await registering.save(
        SUPER, "demo", change(client_secret="other-secret", allowed_networks=SUPPLIER_TEXT)
    )
    new_client = await registering.save(
        SUPER, "demo", change(client_id="Other-client", client_secret=None, allowed_networks="")
    )
    nothing_new = await registering.save(
        SUPER, "demo", change(client_id="Other-client", client_secret=None, allowed_networks="")
    )

    assert (new_secret, new_client, nothing_new) == (Registration.REGISTERED,) * 3
    assert len(registrar.registered) == 4


async def test_the_webhook_target_carries_the_connections_own_list(
    service: ContactCenterConnectionService,
) -> None:
    await service.save(SUPER, "demo", change(allowed_networks=SUPPLIER_TEXT))

    target = await service.webhook_target("demo", WXCC)

    assert target.allowed_networks == SUPPLIED
    assert (await service.webhook_target("andere", WXCC)).allowed_networks == NetworkList()
