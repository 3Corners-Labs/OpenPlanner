"""Storage of the machine API (ADR 0023): switches, clients with groups, tokens."""

from datetime import timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import GroupRow
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.api_clients import (
    AccessToken,
    ApiClient,
    ApiSettings,
    credential_hash,
    new_client_id,
)
from app.domain.authorization import ApiScope, Scope
from tests.support.domain import NOW

READ = frozenset({ApiScope.AVAILABILITY_READ})


def client(organisation: str = "demo", groups: frozenset[str] = frozenset()) -> ApiClient:
    return ApiClient(
        id=None,
        client_id=new_client_id(),
        name=f"Bot {organisation}",
        organisation=organisation,
        groups=groups,
        scopes=READ,
        networks=("198.51.100.0/24", "2001:db8::/32"),
        enabled=True,
        secret_hash=credential_hash("ops_secret"),
        secret_expires_at=NOW + timedelta(days=90),
        created_at=NOW,
    )


def token(client_id: str, n: int, *, expires_in: timedelta = timedelta(minutes=10)) -> AccessToken:
    issued = NOW + timedelta(seconds=n)
    return AccessToken(credential_hash(f"opa_{n}"), client_id, READ, issued, issued + expires_in)


@pytest.fixture
async def uow(engine: AsyncEngine) -> SqlUnitOfWork:
    await seed_demo(engine)
    return SqlUnitOfWork(engine)


async def test_settings_default_off_and_round_trip(uow: SqlUnitOfWork) -> None:
    async with uow.read() as tx:
        assert await tx.get_api_settings() == ApiSettings()
    for settings in (ApiSettings(True, False, NOW), ApiSettings(True, True, NOW)):
        async with uow.locked() as tx:
            await tx.save_api_settings(settings)
        async with uow.read() as tx:
            assert await tx.get_api_settings() == settings


async def test_client_round_trips_with_groups_and_listing_follows_the_scope(
    uow: SqlUnitOfWork,
) -> None:
    async with uow.locked() as tx:
        narrowed = await tx.add_api_client(client(groups=frozenset({"demo"})))
        other = await tx.add_api_client(client("andere"))
    assert narrowed.id is not None
    assert narrowed.groups == {"demo"}

    async with uow.read() as tx:
        loaded = await tx.get_api_client(narrowed.client_id)
        everything = await tx.list_api_clients(Scope(everything=True))
        demo_only = await tx.list_api_clients(Scope(organisations=frozenset({"demo"})))
        groups_only = await tx.list_api_clients(Scope(groups=frozenset({("demo", "demo")})))
        assert await tx.get_api_client("opc_missing-000000000000") is None
    assert loaded == narrowed
    assert {c.client_id for c in everything} == {narrowed.client_id, other.client_id}
    assert [c.client_id for c in demo_only] == [narrowed.client_id]
    assert groups_only == []  # clients belong to an organisation, not a group

    changed = ApiClient(
        **{
            **narrowed.__dict__,
            "name": "Renamed",
            "groups": frozenset(),
            "enabled": False,
            "networks": (),
            "last_token_at": NOW,
        }
    )
    async with uow.locked() as tx:
        await tx.save_api_client(changed)
    async with uow.read() as tx:
        assert await tx.get_api_client(narrowed.client_id) == changed


async def test_unknown_organisation_or_group_is_refused(uow: SqlUnitOfWork) -> None:
    with pytest.raises(LookupError):
        async with uow.locked() as tx:
            await tx.add_api_client(client("nope"))
    with pytest.raises(LookupError):
        async with uow.locked() as tx:
            await tx.add_api_client(client(groups=frozenset({"nope"})))


async def test_a_group_with_clients_cannot_be_removed(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    # Removing the group would silently widen the client to the whole organisation.
    async with uow.locked() as tx:
        await tx.add_api_client(client(groups=frozenset({"demo"})))

    async with AsyncSession(engine) as session:
        removal = delete(GroupRow).where(GroupRow.slug == "demo")
        with pytest.raises(IntegrityError):
            await session.execute(removal)


async def test_tokens_are_stored_found_pruned_and_revoked(uow: SqlUnitOfWork) -> None:
    async with uow.locked() as tx:
        bot = await tx.add_api_client(client())
        other = await tx.add_api_client(client("andere"))
        for n in range(4):
            await tx.add_access_token(token(bot.client_id, n))
        await tx.add_access_token(token(other.client_id, 9, expires_in=timedelta(seconds=-1)))

    async with uow.read() as tx:
        found = await tx.get_access_token(credential_hash("opa_1"))
        assert await tx.get_access_token(credential_hash("opa_guess")) is None
    assert found == token(bot.client_id, 1)

    async with uow.locked() as tx:
        await tx.prune_access_tokens(bot.client_id, NOW + timedelta(seconds=30), keep=2)
    async with uow.read() as tx:
        kept = [await tx.get_access_token(credential_hash(f"opa_{n}")) for n in (0, 1, 2, 3, 9)]
    assert [t is not None for t in kept] == [False, False, True, True, False]

    async with uow.locked() as tx:
        assert await tx.revoke_access_tokens(bot.client_id) == 2
        assert await tx.revoke_access_tokens(bot.client_id) == 0
