"""ApiAccessService: client management by the superuser, tokens and calls by bots (ADR 0023).

Includes the abuse cases of the machine API from docs/threat-model.md: guessed or stolen
credentials, a disabled or rotated client, the emergency stop, scope escalation, another
organisation's group, and token pile-up.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.api_access import CALL_EVENT, TOKEN_EVENT, ApiAccessService
from app.domain.api_clients import (
    ACCESS_TOKEN_LIFETIME,
    MAX_ACTIVE_TOKENS,
    ApiAuthError,
    ApiSettings,
    ClientDraft,
    IssuedSecret,
    credential_hash,
)
from app.domain.audit import Outcome
from app.domain.authorization import ApiScope, Principal, Role
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Organisation
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, make_group

READ = ApiScope.AVAILABILITY_READ
WXCC_EGRESS = "198.51.100.0/24"
INSIDE, OUTSIDE = "198.51.100.7", "203.0.113.9"
NOBODY: frozenset[str] = frozenset()
SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), NOBODY, NOBODY)
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), NOBODY)
ORGANISATIONS = (Organisation(1, "Demo-gemeente", "demo"), Organisation(2, "Andere", "andere"))


def draft(**overrides: object) -> ClientDraft:
    fields: dict[str, object] = {
        "name": "WxCC AI Agent",
        "organisation": "demo",
        "groups": [],
        "scopes": ["availability:read"],
        "networks": [],
    }
    fields.update(overrides)
    return ClientDraft.create(**fields)  # type: ignore[arg-type]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    state = State()
    state.organisations = {o.id: o for o in ORGANISATIONS}
    state.organisation_slugs = {o.id: o.slug for o in ORGANISATIONS}
    state.groups[1] = make_group(id=1, organisation_id=1, slug="demo", name="Demo")
    state.groups[2] = make_group(id=2, organisation_id=1, slug="demo-twee", name="Twee")
    state.groups[3] = make_group(id=3, organisation_id=2, slug="andere-team", name="Andere")
    state.api_settings = ApiSettings(enabled=True)
    return InMemoryUnitOfWork(state)


@pytest.fixture
def service(uow: InMemoryUnitOfWork, clock: FixedClock) -> ApiAccessService:
    return ApiAccessService(uow, clock, AdminService(uow, clock), available=True)


async def register(service: ApiAccessService, **overrides: object) -> IssuedSecret:
    return await service.create_client(SUPER, draft(**overrides), 180)


# --- superuser ---------------------------------------------------------------------------


async def test_superuser_registers_a_client_and_only_the_hash_is_stored(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    issued = await register(service)

    stored = uow.state.api_clients[issued.client.client_id]
    assert stored.secret_hash == credential_hash(issued.secret)
    assert issued.secret not in repr(uow.state)
    assert stored.secret_expires_at == NOW + timedelta(days=180)
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.outcome, event.organisation) == (
        "s-1",
        "api_clients.manage",
        Outcome.SUCCESS,
        "demo",
    )
    assert event.detail == f"client.create,{stored.client_id}"


async def test_only_the_superuser_manages_clients(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError) as caught:
        await service.create_client(ORG_ADMIN, draft(), 180)
    assert caught.value.code is ErrorCode.NOT_PERMITTED
    with pytest.raises(DomainError):
        await service.overview(ORG_ADMIN)
    with pytest.raises(DomainError):
        await service.save_settings(ORG_ADMIN, enabled=True, docs_public=True)
    assert not uow.state.api_clients
    assert [e.outcome for e in uow.state.audit] == [Outcome.DENIED] * 3


@pytest.mark.parametrize(
    ("overrides", "days", "code", "field"),
    [
        ({"organisation": "nope"}, 180, ErrorCode.ORGANISATION_NOT_FOUND, None),
        ({"groups": ["andere-team"]}, 180, ErrorCode.INVALID_CONFIGURATION, "groups"),
        ({"groups": ["nope"]}, 180, ErrorCode.INVALID_CONFIGURATION, "groups"),
        ({}, 3650, ErrorCode.INVALID_CONFIGURATION, "lifetime_days"),
    ],
)
async def test_a_client_cannot_reach_beyond_its_organisation(
    service: ApiAccessService,
    overrides: dict[str, object],
    days: int,
    code: ErrorCode,
    field: str | None,
) -> None:
    with pytest.raises(DomainError) as caught:
        await service.create_client(SUPER, draft(**overrides), days)
    assert (caught.value.code, caught.value.field) == (code, field)


async def test_settings_changes_are_audited_by_name(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    changed = await service.save_settings(SUPER, enabled=True, docs_public=True)

    assert changed == ["api.docs_public"]
    assert uow.state.api_settings == ApiSettings(True, True, NOW)
    assert uow.state.audit[-1].detail == "api.docs_public"
    assert await service.save_settings(SUPER, enabled=True, docs_public=True) == []


async def test_overview_lists_switches_and_clients(service: ApiAccessService) -> None:
    issued = await register(service)

    overview = await service.overview(SUPER)

    assert overview.available
    assert overview.settings.enabled
    assert [c.client_id for c in overview.clients] == [issued.client.client_id]
    assert {o.slug for o in overview.organisations} == {"demo", "andere"}
    assert (await service.client(SUPER, issued.client.client_id)).name == "WxCC AI Agent"
    with pytest.raises(DomainError) as caught:
        await service.client(SUPER, "opc_does-not-exist-0000")
    assert caught.value.code is ErrorCode.API_CLIENT_NOT_FOUND


# --- tokens ------------------------------------------------------------------------------


async def test_valid_credentials_get_a_short_lived_token(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    issued = await register(service)
    client_id = issued.client.client_id

    token = await service.issue_token(client_id, issued.secret, None, INSIDE)

    assert token.expires_in == ACCESS_TOKEN_LIFETIME.total_seconds()
    assert token.scopes == {READ}
    assert credential_hash(token.access_token) in uow.state.access_tokens
    assert token.access_token not in repr(uow.state)
    assert uow.state.api_clients[client_id].last_token_at == NOW
    event = uow.state.audit[-1]
    assert (event.actor, event.action, event.outcome, event.detail) == (
        client_id,
        TOKEN_EVENT,
        Outcome.SUCCESS,
        "availability:read",
    )
    caller = await service.authenticate(token.access_token, INSIDE)
    assert caller.client.client_id == client_id


async def refused(service: ApiAccessService, *args: object) -> ApiAuthError:
    with pytest.raises(ApiAuthError) as caught:
        await service.issue_token(*args)  # type: ignore[arg-type]
    return caught.value


async def test_every_credential_problem_looks_the_same_but_is_audited_apart(
    service: ApiAccessService, uow: InMemoryUnitOfWork, clock: FixedClock
) -> None:
    issued = await register(service)
    client_id = issued.client.client_id
    fenced = await register(service, networks=[WXCC_EGRESS])

    cases = [
        (("opc_unknown-client-000000", issued.secret, None, INSIDE), "unknown_client"),
        (("not a client id\n", issued.secret, None, INSIDE), "unknown_client"),
        ((client_id, "ops_guess", None, INSIDE), "bad_secret"),
        ((fenced.client.client_id, fenced.secret, None, OUTSIDE), "network"),
    ]
    for args, reason in cases:
        error = await refused(service, *args)
        assert (error.code, error.reason) == ("invalid_client", reason)
        event = uow.state.audit[-1]
        assert (event.action, event.outcome, event.reason) == (TOKEN_EVENT, Outcome.DENIED, reason)
        assert issued.secret not in repr(event)
    assert uow.state.audit[-3].actor == "anonymous"  # a malformed id is never stored as actor
    assert not uow.state.access_tokens

    clock.advance(timedelta(days=181))
    error = await refused(service, client_id, issued.secret, None, INSIDE)
    assert error.reason == "secret_expired"


async def test_scope_escalation_is_refused(service: ApiAccessService) -> None:
    issued = await register(service)
    client_id = issued.client.client_id

    unknown = await refused(service, client_id, issued.secret, "bookings:everything", INSIDE)
    assert (unknown.code, unknown.reason) == ("invalid_scope", "unknown_scope")
    token = await service.issue_token(client_id, issued.secret, "availability:read", INSIDE)
    assert token.scopes == {READ}


async def test_a_scope_not_granted_is_refused(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    issued = await register(service)
    client = issued.client
    uow.state.api_clients[client.client_id] = replace(client, scopes=frozenset())

    error = await refused(service, client.client_id, issued.secret, "availability:read", INSIDE)

    assert (error.code, error.reason) == ("invalid_scope", "scope_not_granted")


@pytest.mark.parametrize(("available", "enabled"), [(False, True), (True, False)])
async def test_the_switches_stop_tokens_and_calls(
    uow: InMemoryUnitOfWork, clock: FixedClock, available: bool, enabled: bool
) -> None:
    on = ApiAccessService(uow, clock, AdminService(uow, clock), available=True)
    issued = await on.create_client(SUPER, draft(), 180)
    token = await on.issue_token(issued.client.client_id, issued.secret, None, INSIDE)
    uow.state.api_settings = ApiSettings(enabled=enabled)
    service = ApiAccessService(uow, clock, AdminService(uow, clock), available=available)

    error = await refused(service, issued.client.client_id, issued.secret, None, INSIDE)
    assert (error.code, error.reason) == ("temporarily_unavailable", "api_disabled")
    with pytest.raises(ApiAuthError) as caught:
        await service.authenticate(token.access_token, INSIDE)
    assert caught.value.code == "temporarily_unavailable"


async def test_tokens_expire(service: ApiAccessService, clock: FixedClock) -> None:
    issued = await register(service)
    token = await service.issue_token(issued.client.client_id, issued.secret, None, INSIDE)

    clock.advance(ACCESS_TOKEN_LIFETIME)

    with pytest.raises(ApiAuthError) as caught:
        await service.authenticate(token.access_token, INSIDE)
    assert (caught.value.code, caught.value.reason) == ("invalid_token", "expired")


async def test_unknown_tokens_are_refused_without_audit(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    before = len(uow.state.audit)

    with pytest.raises(ApiAuthError) as caught:
        await service.authenticate("opa_guessed", INSIDE)

    assert (caught.value.code, caught.value.reason) == ("invalid_token", "unknown_token")
    assert len(uow.state.audit) == before


async def test_a_stolen_token_from_another_network_is_refused_and_audited(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    issued = await register(service, networks=[WXCC_EGRESS])
    token = await service.issue_token(issued.client.client_id, issued.secret, None, INSIDE)

    with pytest.raises(ApiAuthError) as caught:
        await service.authenticate(token.access_token, OUTSIDE)

    assert (caught.value.code, caught.value.reason) == ("invalid_token", "network")
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.reason) == (CALL_EVENT, Outcome.DENIED, "network")


async def test_disabling_rotating_and_revoking_end_every_token(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    issued = await register(service)
    client_id = issued.client.client_id

    async def fresh_token(secret: str) -> str:
        return (await service.issue_token(client_id, secret, None, INSIDE)).access_token

    token = await fresh_token(issued.secret)
    await service.set_enabled(SUPER, client_id, False)
    assert not uow.state.access_tokens
    error = await refused(service, client_id, issued.secret, None, INSIDE)
    assert error.reason == "disabled"
    with pytest.raises(ApiAuthError):
        await service.authenticate(token, INSIDE)
    await service.set_enabled(SUPER, client_id, True)
    await service.set_enabled(SUPER, client_id, True)  # no change, no audit

    token = await fresh_token(issued.secret)
    rotated = await service.rotate_secret(SUPER, client_id, 90)
    assert not uow.state.access_tokens
    assert (await refused(service, client_id, issued.secret, None, INSIDE)).reason == "bad_secret"
    token = await fresh_token(rotated.secret)

    assert await service.revoke_tokens(SUPER, client_id) == 1
    with pytest.raises(ApiAuthError):
        await service.authenticate(token, INSIDE)
    details = [e.detail.split(",")[0] for e in uow.state.audit if e.actor == "s-1"]
    assert details == [
        "client.create",
        "client.disable",
        "client.enable",
        "client.rotate",
        "client.revoke_tokens",
    ]


async def test_changing_the_grant_revokes_tokens_but_renaming_does_not(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    issued = await register(service)
    client_id = issued.client.client_id
    await service.issue_token(client_id, issued.secret, None, INSIDE)

    assert await service.update_client(SUPER, client_id, draft(name="Bot")) == ["name"]
    assert uow.state.access_tokens
    changed = await service.update_client(
        SUPER, client_id, draft(name="Bot", groups=["demo"], organisation="andere")
    )
    assert changed == ["groups"]
    assert not uow.state.access_tokens
    assert uow.state.api_clients[client_id].organisation == "demo"  # never moves
    assert await service.update_client(SUPER, client_id, draft(name="Bot", groups=["demo"])) == []


async def test_tokens_do_not_pile_up(service: ApiAccessService, uow: InMemoryUnitOfWork) -> None:
    issued = await register(service)

    for _ in range(MAX_ACTIVE_TOKENS + 3):
        await service.issue_token(issued.client.client_id, issued.secret, None, INSIDE)

    assert len(uow.state.access_tokens) == MAX_ACTIVE_TOKENS


# --- calls -------------------------------------------------------------------------------


async def caller_for(service: ApiAccessService, **overrides: object):  # type: ignore[no-untyped-def]
    issued = await register(service, **overrides)
    token = await service.issue_token(issued.client.client_id, issued.secret, None, INSIDE)
    return await service.authenticate(token.access_token, INSIDE)


async def test_calls_stay_inside_the_grant(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    whole = await caller_for(service)
    narrow = await caller_for(service, groups=["demo"])

    assert (await service.authorize(whole, READ, "demo-twee")).group.slug == "demo-twee"
    assert [g.group.slug for g in await service.groups(whole, READ)] == ["demo", "demo-twee"]
    assert [g.group.slug for g in await service.groups(narrow, READ)] == ["demo"]
    for caller, slug in ((whole, "andere-team"), (narrow, "demo-twee")):
        with pytest.raises(DomainError) as caught:
            await service.authorize(caller, READ, slug)
        assert caught.value.code is ErrorCode.NOT_PERMITTED
        event = uow.state.audit[-1]
        assert (event.actor, event.action, event.outcome, event.group) == (
            caller.client.client_id,
            "availability:read",
            Outcome.DENIED,
            slug,
        )
    with pytest.raises(DomainError) as missing:
        await service.authorize(whole, READ, "nope")
    assert missing.value.code is ErrorCode.GROUP_NOT_FOUND


async def test_a_token_without_the_scope_reaches_nothing(
    service: ApiAccessService, uow: InMemoryUnitOfWork
) -> None:
    caller = await caller_for(service)
    await service.require_scope(caller, READ)
    caller = replace(caller, scopes=frozenset())

    assert await service.groups(caller, READ) == []
    with pytest.raises(DomainError):
        await service.authorize(caller, READ, "demo")
    with pytest.raises(DomainError) as caught:
        await service.require_scope(caller, READ)
    assert caught.value.code is ErrorCode.NOT_PERMITTED
    event = uow.state.audit[-1]
    assert (event.action, event.outcome, event.reason, event.organisation, event.group) == (
        "availability:read",
        Outcome.DENIED,
        "not_permitted",
        "demo",
        None,
    )
