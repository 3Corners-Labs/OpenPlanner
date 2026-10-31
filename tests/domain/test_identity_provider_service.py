"""IdentityProviderService: registration by the superuser, status for the organisation (ADR 0083).

Includes the abuse cases B13 (an org admin registers a provider) and B15 (the private key
and the secret never reach the domain or an audit event).
"""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_provider_service import (
    CREATED_EVENT,
    DELETED_EVENT,
    DISABLED_EVENT,
    ENABLED_EVENT,
    KEY_ROTATED_EVENT,
    TEST_LOGIN_EVENT,
    UPDATED_EVENT,
    IdentityProviderService,
)
from app.domain.identity_providers import KEY_OVERLAP, ClientAuth, ProviderDraft
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.identity_providers import (
    DISCOVERY_URL,
    FakeDiscoveryFetcher,
    FakeSigningKeys,
    discovery_document,
)
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW
from tests.support.principals import principal

NOBODY: frozenset[str] = frozenset()
SUPER = principal("s-1", "S", frozenset({Role.SUPERUSER}), NOBODY, NOBODY)
ORG_ADMIN = principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), NOBODY)
AUDITOR = principal("a-1", "A", frozenset({Role.AUDITOR}), frozenset({"demo"}), NOBODY)
OUTSIDER = principal("x-1", "X", frozenset({Role.ORG_ADMIN}), frozenset({"andere"}), NOBODY)
ORGANISATIONS = (Organisation(1, "Demo-gemeente", "demo"), Organisation(2, "Andere", "andere"))
PLATFORM_DISCOVERY = "https://login.platform.example/.well-known/openid-configuration"
OTHER_ISSUER = "https://login.gem-b.example"
OTHER_DISCOVERY = f"{OTHER_ISSUER}/.well-known/openid-configuration"


def draft(**overrides: str) -> ProviderDraft:
    values = {
        "key": "gem-a",
        "name": "Gemeente A",
        "discovery_url": DISCOVERY_URL,
        "client_id": "openplanner",
        "client_auth": "private_key_jwt",
        "scopes": "openid profile email",
        "entitlement_claim": "openplanner_entitlements",
        "entitlement_prefix": "openplanner",
    }
    values.update(overrides)
    return ProviderDraft.create(**values)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisations = {o.id: o for o in ORGANISATIONS}
    state.organisation_slugs = {o.id: o.slug for o in ORGANISATIONS}
    return InMemoryUnitOfWork(state)


@pytest.fixture
def discovery() -> FakeDiscoveryFetcher:
    fetcher = FakeDiscoveryFetcher()
    fetcher.documents[OTHER_DISCOVERY] = discovery_document(OTHER_ISSUER)
    return fetcher


@pytest.fixture
def keys() -> FakeSigningKeys:
    return FakeSigningKeys()


@pytest.fixture
def changed() -> list[str]:
    return []


@pytest.fixture
def service(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    discovery: FakeDiscoveryFetcher,
    keys: FakeSigningKeys,
    changed: list[str],
) -> IdentityProviderService:
    return IdentityProviderService(
        uow,
        clock,
        AdminService(uow, clock),
        discovery,
        keys,
        limit=lambda: 2,
        platform_discovery_url=lambda: PLATFORM_DISCOVERY,
        on_change=changed.append,
    )


def events(uow: InMemoryUnitOfWork) -> list[tuple[str, str, Outcome, str, str | None, str]]:
    return [
        (e.actor, e.action, e.outcome, e.reason, e.organisation, e.detail) for e in uow.state.audit
    ]


# --- registering ---------------------------------------------------------------------------


async def test_the_superuser_registers_a_provider_switched_off_with_a_key_pair(
    service: IdentityProviderService,
    uow: InMemoryUnitOfWork,
    discovery: FakeDiscoveryFetcher,
    changed: list[str],
) -> None:
    provider = await service.create(SUPER, "demo", draft(), None)

    assert provider.id is not None
    assert (provider.organisation, provider.key, provider.enabled) == ("demo", "gem-a", False)
    assert provider.issuer == "https://login.gem-a.example"
    assert provider.checked_at == NOW
    assert not provider.secret_set
    assert discovery.fetched == [DISCOVERY_URL]
    stored = uow.state.signing_keys["gem-a"]
    assert [k.kid for k in stored] == ["kid-1"]
    assert stored[0].retired_at is None
    assert events(uow)[-1] == ("s-1", CREATED_EVENT, Outcome.SUCCESS, "", "demo", "key:gem-a")
    assert changed == ["gem-a"]


async def test_b15_the_private_key_and_the_secret_never_reach_an_event_or_the_model(
    service: IdentityProviderService, uow: InMemoryUnitOfWork
) -> None:
    await service.create(SUPER, "demo", draft(), None)
    await service.create(
        SUPER,
        "andere",
        draft(key="gem-b", discovery_url=OTHER_DISCOVERY, client_auth="client_secret_basic"),
        "top-secret",
    )

    detail = await service.provider(SUPER, "demo", "gem-a")
    assert "private-1" not in repr(detail)
    assert "private-1" not in repr(uow.state.audit)
    assert "top-secret" not in repr(uow.state.audit)
    assert "top-secret" not in repr(uow.state.identity_providers)
    assert uow.state.provider_secrets["gem-b"] == "top-secret"
    assert uow.state.identity_providers["gem-b"].secret_set


async def test_client_secret_basic_needs_a_secret_and_generates_no_key(
    service: IdentityProviderService, uow: InMemoryUnitOfWork, keys: FakeSigningKeys
) -> None:
    with pytest.raises(DomainError) as caught:
        await service.create(SUPER, "demo", draft(client_auth="client_secret_basic"), None)
    assert (caught.value.code, caught.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "client_secret",
    )

    await service.create(SUPER, "demo", draft(client_auth="client_secret_basic"), "s3cret")

    assert keys.generated == 0
    assert "gem-a" not in uow.state.signing_keys


async def test_b13_only_the_superuser_registers_or_changes_a_provider(
    service: IdentityProviderService, uow: InMemoryUnitOfWork
) -> None:
    await service.create(SUPER, "demo", draft(), None)

    for action in (
        service.create(ORG_ADMIN, "demo", draft(key="gem-c"), None),
        service.update(ORG_ADMIN, "demo", "gem-a", draft(name="Other"), None),
        service.set_enabled(ORG_ADMIN, "demo", "gem-a", True),
        service.delete(ORG_ADMIN, "demo", "gem-a"),
        service.rotate_key(ORG_ADMIN, "demo", "gem-a"),
    ):
        with pytest.raises(DomainError) as caught:
            await action
        assert caught.value.code is ErrorCode.NOT_PERMITTED
    denied = [e for e in uow.state.audit if e.outcome is Outcome.DENIED]
    assert len(denied) == 5
    assert uow.state.identity_providers["gem-a"].name == "Gemeente A"


async def test_the_organisation_sees_its_own_providers_and_nobody_elses(
    service: IdentityProviderService,
) -> None:
    await service.create(SUPER, "demo", draft(), None)

    for viewer in (ORG_ADMIN, AUDITOR, SUPER):
        overview = await service.overview(viewer, "demo")
        assert [p.key for p in overview.providers] == ["gem-a"]
        assert overview.limit == 2
    with pytest.raises(DomainError) as caught:
        await service.overview(OUTSIDER, "demo")
    assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert (await service.overview(OUTSIDER, "andere")).providers == []
    with pytest.raises(DomainError) as missing:
        await service.provider(SUPER, "andere", "gem-a")
    assert missing.value.code is ErrorCode.IDENTITY_PROVIDER_NOT_FOUND


async def test_a_refused_discovery_document_is_audited_with_its_reason(
    service: IdentityProviderService, uow: InMemoryUnitOfWork, discovery: FakeDiscoveryFetcher
) -> None:
    discovery.documents[DISCOVERY_URL] = discovery_document(response_types_supported=["token"])

    with pytest.raises(DomainError) as caught:
        await service.create(SUPER, "demo", draft(), None)

    assert (caught.value.code, caught.value.field) == (ErrorCode.DISCOVERY_REFUSED, "discovery_url")
    assert events(uow)[-1] == (
        "s-1",
        CREATED_EVENT,
        Outcome.FAILURE,
        "no_code_flow",
        "demo",
        "key:gem-a",
    )
    assert "gem-a" not in uow.state.identity_providers


async def test_a_document_that_cannot_be_fetched_is_refused_with_the_guards_reason(
    service: IdentityProviderService, uow: InMemoryUnitOfWork, discovery: FakeDiscoveryFetcher
) -> None:
    discovery.fail = "not_public"

    with pytest.raises(DomainError) as caught:
        await service.create(SUPER, "demo", draft(), None)

    assert caught.value.code is ErrorCode.DISCOVERY_REFUSED
    assert events(uow)[-1][2:4] == (Outcome.FAILURE, "not_public")


async def test_without_an_outbound_fetcher_nothing_can_be_registered(
    uow: InMemoryUnitOfWork, clock: FixedClock, keys: FakeSigningKeys
) -> None:
    service = IdentityProviderService(
        uow,
        clock,
        AdminService(uow, clock),
        None,
        keys,
        limit=lambda: 2,
        platform_discovery_url=lambda: None,
    )

    with pytest.raises(DomainError) as caught:
        await service.create(SUPER, "demo", draft(), None)

    assert caught.value.code is ErrorCode.INTEGRATION_NOT_READY


async def test_an_issuer_is_registered_once_on_the_installation(
    service: IdentityProviderService, discovery: FakeDiscoveryFetcher
) -> None:
    await service.create(SUPER, "demo", draft(), None)

    with pytest.raises(DomainError) as same:
        await service.create(SUPER, "andere", draft(key="gem-b"), None)
    assert same.value.code is ErrorCode.ISSUER_TAKEN
    discovery.documents[PLATFORM_DISCOVERY] = discovery_document("https://login.platform.example")
    with pytest.raises(DomainError) as platform:
        await service.create(
            SUPER, "andere", draft(key="gem-b", discovery_url=PLATFORM_DISCOVERY), None
        )
    assert platform.value.code is ErrorCode.ISSUER_TAKEN


async def test_a_key_is_unique_and_an_organisation_stays_within_its_limit(
    service: IdentityProviderService, discovery: FakeDiscoveryFetcher
) -> None:
    await service.create(SUPER, "demo", draft(), None)
    with pytest.raises(DomainError) as taken:
        await service.create(SUPER, "andere", draft(discovery_url=OTHER_DISCOVERY), None)
    assert taken.value.code is ErrorCode.IDP_KEY_TAKEN

    await service.create(SUPER, "demo", draft(key="gem-a2", discovery_url=OTHER_DISCOVERY), None)
    third = "https://login.gem-c.example"
    discovery.documents[f"{third}/.well-known/openid-configuration"] = discovery_document(third)
    with pytest.raises(DomainError) as full:
        await service.create(
            SUPER,
            "demo",
            draft(key="gem-a3", discovery_url=f"{third}/.well-known/openid-configuration"),
            None,
        )
    assert full.value.code is ErrorCode.IDENTITY_PROVIDER_LIMIT_REACHED


# --- changing --------------------------------------------------------------------------------


async def test_an_update_names_what_changed_and_rechecks_a_new_address(
    service: IdentityProviderService,
    uow: InMemoryUnitOfWork,
    discovery: FakeDiscoveryFetcher,
    clock: FixedClock,
) -> None:
    await service.create(SUPER, "demo", draft(), None)
    clock.advance(timedelta(hours=1))

    unchanged = await service.update(SUPER, "demo", "gem-a", draft(), None)
    changed = await service.update(
        SUPER,
        "demo",
        "gem-a",
        draft(name="Gemeente A (nieuw)", discovery_url=OTHER_DISCOVERY),
        None,
    )

    assert unchanged == []
    assert changed == ["name", "discovery_url"]
    stored = uow.state.identity_providers["gem-a"]
    assert (stored.issuer, stored.checked_at, stored.updated_at) == (
        OTHER_ISSUER,
        NOW + timedelta(hours=1),
        NOW + timedelta(hours=1),
    )
    assert discovery.fetched == [DISCOVERY_URL, OTHER_DISCOVERY]
    assert events(uow)[-1] == (
        "s-1",
        UPDATED_EVENT,
        Outcome.SUCCESS,
        "",
        "demo",
        "name,discovery_url",
    )
    with pytest.raises(DomainError) as caught:
        await service.update(SUPER, "demo", "gem-a", draft(key="other"), None)
    assert caught.value.field == "key"


async def test_switching_the_method_keeps_what_the_new_method_needs(
    service: IdentityProviderService, uow: InMemoryUnitOfWork, keys: FakeSigningKeys
) -> None:
    await service.create(SUPER, "demo", draft(client_auth="client_secret_basic"), "s3cret")

    changed = await service.update(SUPER, "demo", "gem-a", draft(), None)
    assert changed == ["client_auth", "signing_key"]
    assert [k.kid for k in uow.state.signing_keys["gem-a"]] == ["kid-1"]

    blank = await service.update(
        SUPER, "demo", "gem-a", draft(client_auth="client_secret_basic"), None
    )
    assert blank == ["client_auth"]  # the stored secret still serves
    assert uow.state.provider_secrets["gem-a"] == "s3cret"
    replaced = await service.update(
        SUPER, "demo", "gem-a", draft(client_auth="client_secret_basic"), "new-secret"
    )
    assert replaced == ["client_secret"]
    assert uow.state.provider_secrets["gem-a"] == "new-secret"


async def test_switching_on_and_off_is_audited_and_idempotent(
    service: IdentityProviderService, uow: InMemoryUnitOfWork, changed: list[str]
) -> None:
    await service.create(SUPER, "demo", draft(), None)

    assert await service.set_enabled(SUPER, "demo", "gem-a", True)
    assert not await service.set_enabled(SUPER, "demo", "gem-a", True)
    assert await service.set_enabled(SUPER, "demo", "gem-a", False)

    actions = [e[1] for e in events(uow)]
    assert actions[-2:] == [ENABLED_EVENT, DISABLED_EVENT]
    assert changed == ["gem-a", "gem-a", "gem-a"]


async def test_deleting_removes_the_secret_and_the_keys(
    service: IdentityProviderService, uow: InMemoryUnitOfWork
) -> None:
    await service.create(SUPER, "demo", draft(), None)

    await service.delete(SUPER, "demo", "gem-a")

    assert uow.state.identity_providers == {}
    assert uow.state.signing_keys == {}
    assert uow.state.private_keys == {}
    assert events(uow)[-1][1:3] == (DELETED_EVENT, Outcome.SUCCESS)
    with pytest.raises(DomainError):
        await service.delete(SUPER, "demo", "gem-a")


async def test_a_rotation_keeps_the_old_key_published_for_a_day(
    service: IdentityProviderService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    await service.create(SUPER, "demo", draft(), None)
    clock.advance(timedelta(days=1))

    rotated = await service.rotate_key(SUPER, "demo", "gem-a")

    assert rotated.kid == "kid-2"
    detail = await service.provider(SUPER, "demo", "gem-a")
    assert detail.current_key is not None
    assert detail.current_key.kid == "kid-2"
    published = await service.published_keys()
    assert [k["kid"] for k in published["keys"]] == ["kid-1", "kid-2"]
    assert all("d" not in k and "x" in k for k in published["keys"])
    clock.advance(KEY_OVERLAP)
    assert [k["kid"] for k in (await service.published_keys())["keys"]] == ["kid-2"]
    assert events(uow)[-1][1] == KEY_ROTATED_EVENT


async def test_a_provider_with_a_secret_has_no_key_to_rotate(
    service: IdentityProviderService,
) -> None:
    await service.create(SUPER, "demo", draft(client_auth="client_secret_basic"), "s3cret")

    with pytest.raises(DomainError) as caught:
        await service.rotate_key(SUPER, "demo", "gem-a")

    assert caught.value.field == "client_auth"
    assert (await service.published_keys()) == {"keys": []}


async def test_a_test_login_is_audited_with_its_verdict(
    service: IdentityProviderService, uow: InMemoryUnitOfWork
) -> None:
    await service.create(SUPER, "demo", draft(), None)

    await service.record_test_login(SUPER, "demo", "gem-a")
    await service.record_test_login(SUPER, "demo", "gem-a", reason="invalid_token")

    assert events(uow)[-2:] == [
        ("s-1", TEST_LOGIN_EVENT, Outcome.SUCCESS, "", "demo", "key:gem-a"),
        ("s-1", TEST_LOGIN_EVENT, Outcome.FAILURE, "invalid_token", "demo", "key:gem-a"),
    ]
    with pytest.raises(DomainError):
        await service.record_test_login(ORG_ADMIN, "demo", "gem-a")


def test_the_methods_know_what_they_need() -> None:
    assert ClientAuth.PRIVATE_KEY_JWT.needs_key
    assert not ClientAuth.PRIVATE_KEY_JWT.needs_secret
    assert ClientAuth.CLIENT_SECRET_BASIC.needs_secret
