"""API clients read from and written to the database for a configuration document (ADR 0099)."""

from dataclasses import replace
from hashlib import sha256

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_clients import WRITERS, read_api_clients
from app.adapters.db.models import ApiAccessTokenRow, ApiClientGroupRow, ApiClientRow
from app.domain.configuration_changes import ChangeAction, RuleContext, Usage
from app.domain.configuration_document import Document, Mode, Scope
from app.domain.configuration_plan import plan_document
from app.domain.configuration_v1 import V1
from app.domain.configuration_values import Secret
from app.domain.errors import DomainError, ErrorCode
from tests.support.config_seed import (
    CLIENT_ID,
    NOW,
    SECRET_HASH,
    apply_document,
    change,
    installation,
    seed,
)
from tests.support.configuration import allow

OTHER = "opc_" + "b" * 20
OTHER_HASH = sha256(b"other").hexdigest()


@pytest.fixture
async def seeded(engine: AsyncEngine) -> AsyncEngine:
    await seed(engine)
    return engine


async def add_token(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        pk = await session.scalar(
            select(ApiClientRow.id).where(ApiClientRow.client_id == CLIENT_ID)
        )
        session.add(
            ApiAccessTokenRow(
                token_hash="t" * 64,
                client_pk=pk,
                scopes="availability:read",
                issued_at=NOW,
                expires_at=NOW.replace(year=2027),
            )
        )


async def tokens(engine: AsyncEngine) -> int:
    async with AsyncSession(engine) as session:
        return int(await session.scalar(select(func.count()).select_from(ApiAccessTokenRow)) or 0)


def document(client: dict[str, object]) -> dict[str, object]:
    return {"api_clients": [{"client_id": CLIENT_ID, **client}]}


async def stored(engine: AsyncEngine) -> ApiClientRow:
    async with AsyncSession(engine) as session:
        row = await session.scalar(select(ApiClientRow).where(ApiClientRow.client_id == CLIENT_ID))
        assert row is not None
        return row


async def test_a_client_is_read_with_its_hash_as_a_secret(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as session:
        (client,) = await read_api_clients(session, None)

    assert client.key == (CLIENT_ID,)
    assert client.fields["name"] == "Bot"
    assert client.fields["organisation"] == "acme"
    assert client.fields["scopes"] == ["availability:read"]
    assert client.fields["networks"] == ["10.0.0.0/8"]
    assert client.fields["groups"] == ["helpdesk"]
    assert client.fields["enabled"] is True
    assert client.fields["secret_hash"] == Secret(SECRET_HASH)
    assert SECRET_HASH not in repr(client.fields["secret_hash"])
    assert client.fields["secret_expires_at"] == "2027-01-01T00:00:00+00:00"


async def test_what_is_read_is_valid_by_the_rules_of_the_format(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as session:
        full = await installation(session)
    without = replace(full, children={**full.children, "api_clients": ()})
    document_ = Document(V1.name, Mode.MERGE, Scope(), full, "digest", V1)

    created = plan_document(
        document_,
        without,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(now=NOW),
        settings_version=1,
    )
    unchanged = plan_document(
        document_,
        full,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(now=NOW),
        settings_version=1,
    )

    assert created.refusals == ()
    assert [c.kind for c in created.changes] == ["api_client"]
    assert unchanged.refusals == ()
    assert unchanged.changes == ()


async def test_a_client_is_created_with_its_groups_and_hash(seeded: AsyncEngine) -> None:
    data = {
        "api_clients": [
            {
                "client_id": OTHER,
                "name": "Second",
                "organisation": "acme",
                "scopes": ["availability:read"],
                "groups": ["loket"],
                "enabled": True,
                "secret_hash": {"sealed": OTHER_HASH},
                "secret_expires_at": "2027-02-01T00:00:00+00:00",
            }
        ]
    }

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    async with AsyncSession(seeded) as session:
        clients = {c.key[0]: c for c in await read_api_clients(session, None)}
    created = clients[OTHER]
    assert created.fields["groups"] == ["loket"]
    assert created.fields["secret_hash"] == Secret(OTHER_HASH)
    assert created.fields["enabled"] is True


async def test_a_name_change_keeps_the_tokens_and_any_other_change_revokes_them(
    seeded: AsyncEngine,
) -> None:
    await add_token(seeded)

    renamed = await apply_document(seeded, document({"name": "Renamed"}))
    assert renamed.ok, renamed.refusals
    assert (await stored(seeded)).name == "Renamed"
    assert await tokens(seeded) == 1

    widened = await apply_document(
        seeded, document({"groups": ["helpdesk", "loket"], "enabled": False})
    )
    assert widened.ok, widened.refusals
    assert await tokens(seeded) == 0
    row = await stored(seeded)
    assert row.enabled is False
    async with AsyncSession(seeded) as session:
        grants = await session.scalar(select(func.count()).select_from(ApiClientGroupRow))
    assert grants == 2


async def test_scopes_networks_and_the_secret_are_replaced(seeded: AsyncEngine) -> None:
    data = document(
        {
            "scopes": ["availability:read", "configuration:transfer"],
            "networks": [],
            "secret_hash": {"sealed": OTHER_HASH},
            "secret_expires_at": "2027-03-01T00:00:00+00:00",
        }
    )

    plan = await apply_document(seeded, data)

    assert plan.ok, plan.refusals
    row = await stored(seeded)
    assert row.scopes == "availability:read configuration:transfer"
    assert row.networks == ""
    assert row.secret_hash == OTHER_HASH
    assert row.secret_expires_at.isoformat().startswith("2027-03-01")


async def test_a_disabled_client_is_deleted_with_its_grants_and_tokens(seeded: AsyncEngine) -> None:
    await add_token(seeded)
    await apply_document(seeded, document({"enabled": False}))

    plan = await apply_document(seeded, document({"action": "delete"}))

    assert plan.ok, plan.refusals
    async with AsyncSession(seeded) as session:
        assert await session.scalar(select(func.count()).select_from(ApiClientRow)) == 0
        assert await session.scalar(select(func.count()).select_from(ApiClientGroupRow)) == 0
    assert await tokens(seeded) == 0


async def test_an_enabled_client_is_not_deleted(seeded: AsyncEngine) -> None:
    plan = await apply_document(seeded, document({"action": "delete"}))

    assert [r.code for r in plan.refusals] == [ErrorCode.API_CLIENT_ENABLED]


async def test_a_writer_refuses_what_does_not_resolve(seeded: AsyncEngine) -> None:
    values = {
        "name": "X",
        "organisation": "nope",
        "secret_hash": Secret(OTHER_HASH),
        "secret_expires_at": "2027-02-01T00:00:00+00:00",
    }
    writer = WRITERS["api_client"]
    async with AsyncSession(seeded) as session, session.begin():
        with pytest.raises(DomainError) as organisation:
            await writer(session, None, change("api_client", (OTHER,), ChangeAction.CREATE, values))
        with pytest.raises(DomainError) as missing:
            await writer(
                session, None, change("api_client", (OTHER,), ChangeAction.UPDATE, {"name": "Y"})
            )
        with pytest.raises(DomainError) as moved:
            await writer(
                session,
                None,
                change(
                    "api_client", (CLIENT_ID,), ChangeAction.UPDATE, {"organisation": "elsewhere"}
                ),
            )
        with pytest.raises(DomainError) as group:
            await writer(
                session,
                None,
                change("api_client", (CLIENT_ID,), ChangeAction.UPDATE, {"groups": ["nope"]}),
            )
        with pytest.raises(DomainError) as unopened:
            await writer(
                session,
                None,
                change("api_client", (CLIENT_ID,), ChangeAction.UPDATE, {"secret_hash": "plain"}),
            )
    assert organisation.value.field == "organisation"
    assert missing.value.code is ErrorCode.API_CLIENT_NOT_FOUND
    assert moved.value.field == "organisation"
    assert group.value.field == "groups"
    assert unopened.value.field == "secret_hash"
