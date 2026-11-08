"""Identity providers in a configuration document: reading them and applying changes."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_login import (
    WRITERS,
    ensure_signing_keys,
    read_identity_providers,
    write_identity_provider,
)
from app.adapters.db.identity_providers import ProviderCredentialStore
from app.adapters.db.models import (
    IdentityProviderDomainRow,
    IdentityProviderKeyRow,
    IdentityProviderRow,
    OrganisationRow,
)
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Entity
from app.domain.configuration_values import Secret, Value
from app.domain.errors import DomainError, ErrorCode
from tests.support.config_changes import apply_changes, planned
from tests.support.config_roundtrip import document_of, organisations, plan_against

BOX = SecretBox(b"k" * 32)
URL = "https://idp.voorbeeld.nl/.well-known/openid-configuration"
BASIC: dict[str, Value] = {
    "name": "Intern",
    "discovery_url": URL,
    "client_id": "openplanner",
    "client_auth": "client_secret_basic",
    "scopes": "openid profile",
    "entitlement_claim": "groups",
    "entitlement_prefix": "op",
    "enabled": True,
    "mfa_policy": "record",
    "mfa_amr_values": "",
    "mfa_acr_values": "",
    "client_secret": Secret("a-long-client-secret"),
}
JWT: dict[str, Value] = {
    **BASIC,
    "name": "Sleutel",
    "client_auth": "private_key_jwt",
    "client_secret": None,
}
KEY: dict[str, Value] = {
    "public_jwk": {"kty": "EC", "x": "a"},
    "private_key": Secret("-----PEM-----"),
    "created_at": "2026-11-01T10:00:00+00:00",
    "retired_at": None,
}


def change(
    kind: str,
    address: tuple[str, ...],
    action: ChangeAction,
    values: dict[str, Value],
    fields: tuple[str, ...] | None = None,
) -> Change:
    return planned(kind, address, action, values, fields, organisation=address[0])


def provider(key: str, action: ChangeAction, values: dict[str, Value], *fields: str) -> Change:
    return change("identity_provider", ("acme", key), action, values, fields or None)


async def apply(
    engine: AsyncEngine, box: SecretBox | None, *changes: Change, ensure: bool = False
) -> None:
    await apply_changes(
        engine, WRITERS, box, *changes, after=ensure_signing_keys if ensure else None
    )


async def read(engine: AsyncEngine, box: SecretBox | None = BOX) -> tuple[Entity, ...]:
    async with AsyncSession(engine) as session:
        return (await read_identity_providers(session, box))["acme"]


@pytest.fixture
async def installation(engine: AsyncEngine) -> AsyncEngine:
    async with AsyncSession(engine) as session, session.begin():
        session.add(OrganisationRow(name="Acme", slug="acme"))
    return engine


async def seed(engine: AsyncEngine) -> None:
    """A basic provider with a key and a domain, and a jwt provider that gets its own key."""
    await apply(
        engine,
        BOX,
        provider("intern", ChangeAction.CREATE, {**BASIC, "issuer": "https://idp.voorbeeld.nl"}),
        provider("sleutel", ChangeAction.CREATE, {**JWT, "issuer": "https://idp2.voorbeeld.nl"}),
        change("identity_provider_key", ("acme", "intern", "kid-1"), ChangeAction.CREATE, KEY),
        change(
            "identity_provider_domain",
            ("acme", "intern", "voorbeeld.nl"),
            ChangeAction.CREATE,
            {},
        ),
        ensure=True,
    )


async def test_the_reader_returns_providers_with_keys_domains_and_opened_secrets(
    installation: AsyncEngine,
) -> None:
    await seed(installation)

    intern, sleutel = await read(installation)

    assert (intern.kind, intern.key) == ("identity_provider", ("intern",))
    assert intern.fields["client_secret"] == Secret("a-long-client-secret")
    assert intern.fields["enabled"] is True
    assert intern.fields["scopes"] == "openid profile"
    (key,) = intern.children["keys"]
    assert key.key == ("kid-1",)
    assert key.fields["private_key"] == Secret("-----PEM-----")
    assert key.fields["created_at"] == "2026-11-01T10:00:00+00:00"
    assert key.fields["retired_at"] is None
    assert [d.key for d in intern.children["domains"]] == [("voorbeeld.nl",)]
    assert sleutel.fields["client_secret"] is None
    (own,) = sleutel.children["keys"]  # ensure_signing_keys generated one
    assert isinstance(own.fields["private_key"], Secret)


async def test_a_reader_without_a_settings_key_shows_no_secret(installation: AsyncEngine) -> None:
    await seed(installation)

    intern = (await read(installation, None))[0]

    assert intern.fields["client_secret"] is None
    assert intern.children["keys"][0].fields["private_key"] is None


async def test_what_is_written_opens_for_the_oidc_adapter(installation: AsyncEngine) -> None:
    await seed(installation)

    store = ProviderCredentialStore(installation, BOX)
    basic = await store.credentials("intern")
    jwt = await store.credentials("sleutel")

    assert basic is not None
    assert basic.client_secret == "a-long-client-secret"  # noqa: S105
    assert (basic.kid, basic.private_pem) == ("kid-1", "-----PEM-----")
    assert jwt is not None
    assert jwt.kid is not None
    assert jwt.private_pem is not None
    assert jwt.private_pem.startswith("-----BEGIN PRIVATE KEY-----")


@pytest.mark.parametrize("secrets", [True, False])
async def test_a_document_of_what_was_read_changes_nothing(
    installation: AsyncEngine, secrets: bool
) -> None:
    await seed(installation)
    tree = organisations(acme={"identity_providers": await read(installation)})

    plan = plan_against(document_of(tree, secrets=secrets), tree)

    assert plan.refusals == ()
    assert plan.changes == ()


async def test_an_update_writes_only_the_fields_it_names_and_the_issuer_it_is_given(
    installation: AsyncEngine,
) -> None:
    await seed(installation)
    before = (await read(installation))[0]
    values = {**BASIC, "name": "Nieuw", "issuer": "https://nieuw.voorbeeld.nl"}

    await apply(installation, BOX, provider("intern", ChangeAction.UPDATE, values, "name"))

    after = (await read(installation))[0]
    assert after.fields["name"] == "Nieuw"
    assert {k: v for k, v in after.fields.items() if k != "name"} == {
        k: v for k, v in before.fields.items() if k != "name"
    }
    async with AsyncSession(installation) as session:
        row = await session.scalar(
            select(IdentityProviderRow).where(IdentityProviderRow.key == "intern")
        )
    assert row is not None
    assert row.issuer == "https://nieuw.voorbeeld.nl"


async def test_a_secret_is_replaced_or_cleared_only_when_named(installation: AsyncEngine) -> None:
    await seed(installation)
    other: dict[str, Value] = {**BASIC, "client_secret": Secret("another-secret-value")}
    none: dict[str, Value] = {**BASIC, "client_secret": None}

    await apply(installation, BOX, provider("intern", ChangeAction.UPDATE, BASIC, "name"))
    assert (await read(installation))[0].fields["client_secret"] == Secret("a-long-client-secret")
    await apply(installation, BOX, provider("intern", ChangeAction.UPDATE, other, "client_secret"))
    assert (await read(installation))[0].fields["client_secret"] == Secret("another-secret-value")
    await apply(installation, BOX, provider("intern", ChangeAction.UPDATE, none, "client_secret"))
    assert (await read(installation))[0].fields["client_secret"] is None


async def test_a_key_is_updated_retired_and_deleted(installation: AsyncEngine) -> None:
    await seed(installation)
    address = ("acme", "intern", "kid-1")
    values: dict[str, Value] = {
        "public_jwk": {"kty": "EC", "x": "b"},
        "private_key": Secret("new-pem"),
        "created_at": "2026-11-02T10:00:00+00:00",
        "retired_at": "2026-11-03T10:00:00+00:00",
    }

    await apply(
        installation, BOX, change("identity_provider_key", address, ChangeAction.UPDATE, values)
    )

    assert (await read(installation))[0].children["keys"][0].fields == values
    await apply(
        installation, BOX, change("identity_provider_key", address, ChangeAction.DELETE, {})
    )
    assert (await read(installation))[0].children["keys"] == ()


async def test_a_domain_starts_pending_with_a_token_and_keeps_its_state(
    installation: AsyncEngine,
) -> None:
    await seed(installation)
    async with AsyncSession(installation) as session, session.begin():
        row = await session.scalar(select(IdentityProviderDomainRow))
        assert row is not None
        assert row.state == "pending"
        assert len(row.token) >= 32
        row.state = "verified"
        row.verified_at = datetime(2026, 11, 2, tzinfo=UTC)
    address = ("acme", "intern", "voorbeeld.nl")

    await apply(
        installation, BOX, change("identity_provider_domain", address, ChangeAction.CREATE, {})
    )

    async with AsyncSession(installation) as session:
        rows = (await session.scalars(select(IdentityProviderDomainRow))).all()
    assert [(r.domain, r.state) for r in rows] == [("voorbeeld.nl", "verified")]
    await apply(
        installation, BOX, change("identity_provider_domain", address, ChangeAction.DELETE, {})
    )
    assert (await read(installation))[0].children["domains"] == ()


async def test_deleting_a_provider_takes_its_keys_and_domains(installation: AsyncEngine) -> None:
    await seed(installation)

    await apply(installation, BOX, provider("intern", ChangeAction.DELETE, {}))

    async with AsyncSession(installation) as session:
        providers = await session.scalar(select(func.count()).select_from(IdentityProviderRow))
        kids = (await session.scalars(select(IdentityProviderKeyRow.kid))).all()
        domains = await session.scalar(select(func.count()).select_from(IdentityProviderDomainRow))
    assert (providers, domains) == (1, 0)
    assert "kid-1" not in kids


async def test_ensure_signing_keys_leaves_a_provider_with_a_key_alone(
    installation: AsyncEngine,
) -> None:
    await seed(installation)

    async with AsyncSession(installation) as session, session.begin():
        created = await ensure_signing_keys(session, BOX)

    assert created == []


async def test_a_provider_without_an_issuer_is_refused(installation: AsyncEngine) -> None:
    with pytest.raises(DomainError) as refused:
        await apply(installation, BOX, provider("intern", ChangeAction.CREATE, dict(BASIC)))

    assert (refused.value.code, refused.value.field) == (ErrorCode.INVALID_CONFIGURATION, "issuer")


async def test_a_secret_without_a_settings_key_is_refused(installation: AsyncEngine) -> None:
    values = {**BASIC, "issuer": "https://idp.voorbeeld.nl"}

    with pytest.raises(DomainError) as refused:
        await apply(installation, None, provider("intern", ChangeAction.CREATE, values))

    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY


def _bad_changes() -> list[Change]:
    jwk: dict[str, Value] = {"public_jwk": {}, "private_key": Secret("p")}
    return [
        provider("nope", ChangeAction.UPDATE, dict(BASIC), "name"),
        provider("nope", ChangeAction.DELETE, {}),
        change("identity_provider_key", ("acme", "nope", "k"), ChangeAction.CREATE, jwk),
        change(
            "identity_provider_key",
            ("acme", "intern", "ghost"),
            ChangeAction.UPDATE,
            {"retired_at": None},
        ),
        change("identity_provider_domain", ("acme", "nope", "x.nl"), ChangeAction.CREATE, {}),
        change(
            "identity_provider_key",
            ("acme", "intern", "k2"),
            ChangeAction.CREATE,
            {"public_jwk": {}, "private_key": None},
        ),
    ]


async def test_an_unknown_organisation_provider_key_or_secret_is_refused(
    installation: AsyncEngine,
) -> None:
    await seed(installation)

    for one in _bad_changes():
        with pytest.raises(DomainError) as refused:
            await apply(installation, BOX, one)
        assert refused.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_an_unknown_organisation_is_refused_as_such(installation: AsyncEngine) -> None:
    unknown = change(
        "identity_provider", ("zzz", "x"), ChangeAction.CREATE, {**BASIC, "issuer": "https://i"}
    )

    with pytest.raises(DomainError) as refused:
        await apply(installation, BOX, unknown)

    assert refused.value.code is ErrorCode.ORGANISATION_NOT_FOUND


async def test_deleting_what_is_already_gone_is_not_an_error(installation: AsyncEngine) -> None:
    await seed(installation)

    await apply(
        installation,
        BOX,
        change("identity_provider_key", ("acme", "intern", "ghost"), ChangeAction.DELETE, {}),
        change("identity_provider_domain", ("acme", "intern", "ghost.nl"), ChangeAction.DELETE, {}),
    )


async def test_a_key_without_dates_gets_now_and_an_update_can_set_a_date(
    installation: AsyncEngine,
) -> None:
    await seed(installation)
    values: dict[str, Any] = {
        "public_jwk": {"k": "v"},
        "private_key": Secret("p"),
        "created_at": None,
        "retired_at": None,
    }
    address = ("acme", "intern", "kid-2")
    dated = {**values, "created_at": "2026-01-01T00:00:00+00:00"}

    await apply(
        installation, BOX, change("identity_provider_key", address, ChangeAction.CREATE, values)
    )
    await apply(
        installation,
        BOX,
        change("identity_provider_key", address, ChangeAction.UPDATE, dated, ("created_at",)),
    )

    keys = {k.key[0]: k for k in (await read(installation))[0].children["keys"]}
    assert keys["kid-2"].fields["created_at"] == "2026-01-01T00:00:00+00:00"
    assert keys["kid-2"].fields["public_jwk"] == {"k": "v"}


async def test_the_provider_writer_works_on_its_own(installation: AsyncEngine) -> None:
    values = {**BASIC, "issuer": "https://idp.voorbeeld.nl"}
    async with AsyncSession(installation) as session, session.begin():
        await write_identity_provider(session, BOX, provider("p", ChangeAction.CREATE, values))

    assert [e.key for e in await read(installation)] == [("p",)]
