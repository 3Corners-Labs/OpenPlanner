"""SQL queries of the organisation identity providers: sealed secrets and keys (ADR 0083)."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.identity_providers import ProviderCredentialStore
from app.adapters.db.models import IdentityProviderKeyRow, IdentityProviderRow
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_providers import KEY_OVERLAP, ClientAuth, OrganisationProvider
from tests.fakes.identity_providers import DISCOVERY_URL, ISSUER, FakeSigningKeys
from tests.support.domain import NOW

BOX = SecretBox(b"k" * 32)


def provider(
    key: str = "gem-a", organisation: str = "demo", **overrides: object
) -> OrganisationProvider:
    values: dict[str, object] = {
        "id": None,
        "organisation": organisation,
        "key": key,
        "name": "Gemeente A",
        "discovery_url": DISCOVERY_URL,
        "issuer": ISSUER,
        "client_id": "openplanner",
        "client_auth": ClientAuth.CLIENT_SECRET_BASIC,
        "scopes": "openid profile email",
        "entitlement_claim": "openplanner_entitlements",
        "entitlement_prefix": "openplanner",
        "enabled": False,
        "secret_set": True,
        "created_at": NOW,
        "updated_at": NOW,
        "checked_at": NOW,
    }
    values.update(overrides)
    return OrganisationProvider(**values)  # type: ignore[arg-type]


@pytest.fixture
async def uow(engine: AsyncEngine) -> SqlUnitOfWork:
    await seed_demo(engine)
    return SqlUnitOfWork(engine, BOX)


async def test_a_provider_round_trips_and_its_secret_is_sealed(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    async with uow.locked() as tx:
        stored = await tx.add_identity_provider(provider(), "s3cret")

    async with uow.read() as tx:
        found = await tx.get_identity_provider("gem-a")
        listed = await tx.list_identity_providers(Scope(everything=True))
        own = await tx.list_identity_providers(Scope(organisations=frozenset({"demo"})))
        other = await tx.list_identity_providers(Scope(organisations=frozenset({"andere"})))
    assert found == stored
    assert stored.id is not None
    assert stored.secret_set
    assert listed == own == [stored]
    assert other == []
    async with AsyncSession(engine) as session:
        row = (await session.scalars(select(IdentityProviderRow))).one()
    assert row.secret_ciphertext is not None
    assert b"s3cret" not in row.secret_ciphertext
    assert BOX.open("idp:gem-a:secret", row.secret_nonce, row.secret_ciphertext) == b"s3cret"


async def test_saving_keeps_the_secret_unless_a_new_one_is_given(uow: SqlUnitOfWork) -> None:
    async with uow.locked() as tx:
        stored = await tx.add_identity_provider(provider(), "first")
    store = ProviderCredentialStore(uow._engine, BOX)  # noqa: SLF001

    async with uow.locked() as tx:
        await tx.save_identity_provider(
            provider(
                id=stored.id, name="Renamed", enabled=True, updated_at=NOW + timedelta(hours=1)
            ),
            None,
        )
    first = await store.credentials("gem-a")
    async with uow.locked() as tx:
        await tx.save_identity_provider(provider(id=stored.id), "second")
    second = await store.credentials("gem-a")

    assert first is not None
    assert first.client_secret == "first"
    assert first.provider.name == "Renamed"
    assert first.provider.enabled
    assert second is not None
    assert second.client_secret == "second"
    assert (first.kid, first.private_pem) == (None, None)


async def test_keys_are_sealed_published_in_the_window_and_the_current_one_is_opened(
    uow: SqlUnitOfWork, engine: AsyncEngine
) -> None:
    keys = FakeSigningKeys()
    async with uow.locked() as tx:
        await tx.add_identity_provider(
            provider(client_auth=ClientAuth.PRIVATE_KEY_JWT, secret_set=False), None
        )
        await tx.add_signing_key("gem-a", keys.generate(), NOW)
    later = NOW + timedelta(days=1)
    async with uow.locked() as tx:
        await tx.add_signing_key("gem-a", keys.generate(), later)
        await tx.retire_signing_keys("gem-a", keep="kid-2", at=later)

    async with uow.read() as tx:
        listed = await tx.list_signing_keys("gem-a")
        within = await tx.list_published_keys(later + KEY_OVERLAP - timedelta(seconds=1))
        after = await tx.list_published_keys(later + KEY_OVERLAP)
    assert [(k.kid, k.retired_at) for k in listed] == [("kid-2", None), ("kid-1", later)]
    assert sorted(k.kid for k in within) == ["kid-1", "kid-2"]
    assert [k.kid for k in after] == ["kid-2"]
    assert listed[0].public_jwk == {
        "kty": "EC",
        "crv": "P-256",
        "x": "x2",
        "y": "y2",
        "use": "sig",
        "alg": "ES256",
    }
    opened = await ProviderCredentialStore(engine, BOX).credentials("gem-a")
    assert opened is not None
    assert (opened.kid, opened.client_secret) == ("kid-2", None)
    assert opened.private_pem is not None
    assert "private-2" in opened.private_pem
    async with AsyncSession(engine) as session:
        rows = list(await session.scalars(select(IdentityProviderKeyRow)))
    assert all(b"private-" not in r.key_ciphertext for r in rows)


async def test_deleting_takes_the_keys_with_it(uow: SqlUnitOfWork, engine: AsyncEngine) -> None:
    async with uow.locked() as tx:
        await tx.add_identity_provider(provider(client_auth=ClientAuth.PRIVATE_KEY_JWT), None)
        await tx.add_signing_key("gem-a", FakeSigningKeys().generate(), NOW)

    async with uow.locked() as tx:
        assert await tx.delete_identity_provider("gem-a")
        assert not await tx.delete_identity_provider("gem-a")

    async with AsyncSession(engine) as session:
        assert list(await session.scalars(select(IdentityProviderKeyRow))) == []
        assert list(await session.scalars(select(IdentityProviderRow))) == []
    assert await ProviderCredentialStore(engine, BOX).credentials("gem-a") is None


async def test_without_a_settings_key_no_secret_or_key_can_be_stored(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine, None)

    with pytest.raises(DomainError) as caught:
        async with uow.locked() as tx:
            await tx.add_identity_provider(provider(), "s3cret")
    assert caught.value.code is ErrorCode.INTEGRATION_NOT_READY

    async with uow.locked() as tx:
        await tx.add_identity_provider(provider(secret_set=False), None)
    with pytest.raises(DomainError):
        async with uow.locked() as tx:
            await tx.add_signing_key("gem-a", FakeSigningKeys().generate(), NOW)
    opened = await ProviderCredentialStore(engine, None).credentials("gem-a")
    assert opened is not None
    assert opened.client_secret is None


async def test_an_unknown_organisation_is_a_lookup_error(uow: SqlUnitOfWork) -> None:
    with pytest.raises(LookupError):
        async with uow.locked() as tx:
            await tx.add_identity_provider(provider(organisation="nope"), None)
