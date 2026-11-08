"""A configuration document end to end on SQLite (ADR 0099): export, plan, apply, export again.

The installation is written as rows, exported as a document with its secrets sealed, applied to
an empty installation under another settings key, and exported again: the two documents must
say the same. That is the property the SQLite package could not give across versions.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.adapters.crypto import SecretBox
from app.adapters.db.config_clients import read_api_clients
from app.adapters.db.engine import create_engine
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.authorization import Role
from app.domain.configuration_changes import RuleContext
from app.domain.configuration_document import Scope
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.support.config_seed import seed
from tests.support.database import migrated_database
from tests.support.principals import principal

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PASSPHRASE = "a long enough passphrase"
HOST = Actor.host()


def service(
    engine: AsyncEngine, box: SecretBox | None, **limits: int
) -> ConfigurationDocumentService:
    clock = FixedClock(NOW)
    uow = SqlUnitOfWork(engine, box)
    return ConfigurationDocumentService(
        uow,
        clock,
        AdminService(uow, clock),
        JsonCodec(),
        PassphraseSealing(),
        installation="test",
        app_version="0.0.0",
        context=lambda: RuleContext(),
        locked=lambda: frozenset(),
        max_bytes=lambda: 8 * 1024 * 1024,
        max_changes=lambda: limits.get("changes", 500),
    )


def body(document: bytes) -> dict[str, object]:
    """Return the content of a document without its header."""
    data = json.loads(document)
    for name in ("meta", "seal"):
        data.pop(name, None)
    return data  # type: ignore[no-any-return]


@pytest.fixture
async def source(database_url: str) -> AsyncEngine:
    engine = create_engine(database_url, busy_timeout_ms=200)
    await seed(engine)
    yield engine  # type: ignore[misc]
    await engine.dispose()


@pytest.fixture
async def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncEngine:
    engine = create_engine(migrated_database(tmp_path, monkeypatch, "target"), busy_timeout_ms=200)
    yield engine  # type: ignore[misc]
    await engine.dispose()


async def test_an_exported_installation_applies_to_an_empty_one_and_exports_the_same(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    box_a, box_b = SecretBox(b"a" * 32), SecretBox(b"b" * 32)
    exported = await service(source, box_a).export(HOST, Scope(), PASSPHRASE, secrets=True)

    result = await service(target, box_b).apply(HOST, exported, PASSPHRASE, None)

    assert result.plan.refusals == (), result.plan.refusals
    assert result.applied
    again = await service(target, box_b).export(HOST, Scope(), PASSPHRASE, secrets=True)
    assert body(again).keys() == body(exported).keys()
    plain_a = await service(source, box_a).export(HOST, Scope(), None, secrets=False)
    plain_b = await service(target, box_b).export(HOST, Scope(), None, secrets=False)
    assert body(plain_a) == body(plain_b)
    async with AsyncSession(target) as session:
        clients = await read_api_clients(session, box_b)
    assert [c.key for c in clients] == [("opc_" + "a" * 20,)]


async def test_applying_the_same_document_twice_changes_nothing_the_second_time(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    box = SecretBox(b"b" * 32)
    exported = await service(source, SecretBox(b"a" * 32)).export(
        HOST, Scope(), PASSPHRASE, secrets=True
    )
    await service(target, box).apply(HOST, exported, PASSPHRASE, None)

    second = await service(target, box).plan(HOST, exported, PASSPHRASE)

    assert second.refusals == ()
    assert second.changes == (), second.lines()


async def test_a_document_with_deletions_needs_the_hash_of_the_reviewed_plan(
    source: AsyncEngine,
) -> None:
    box = SecretBox(b"a" * 32)
    data = json.dumps(
        {
            "format": "openplanner.config/v1",
            "mode": "replace",
            "organisations": [{"slug": "acme", "groups": [{"slug": "helpdesk"}]}],
        }
    ).encode()
    svc = service(source, box)
    plan = await svc.plan(HOST, data, None)
    assert [(c.kind, c.action.value) for c in plan.changes] == [("group", "delete")]

    with pytest.raises(DomainError) as missing:
        await svc.apply(HOST, data, None, None)
    with pytest.raises(DomainError) as wrong:
        await svc.apply(HOST, data, None, "0" * 64)

    assert missing.value.code is ErrorCode.CONFIGURATION_PLAN_STALE
    assert wrong.value.code is ErrorCode.CONFIGURATION_PLAN_STALE


async def test_a_plan_with_more_changes_than_the_limit_is_refused(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    exported = await service(source, SecretBox(b"a" * 32)).export(
        HOST, Scope(), PASSPHRASE, secrets=True
    )

    with pytest.raises(DomainError) as refused:
        await service(target, SecretBox(b"b" * 32), changes=2).apply(
            HOST, exported, PASSPHRASE, None
        )

    assert refused.value.code is ErrorCode.CONFIGURATION_TOO_MANY_CHANGES


async def test_a_wrong_passphrase_is_refused_before_anything_is_planned(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    exported = await service(source, SecretBox(b"a" * 32)).export(
        HOST, Scope(), PASSPHRASE, secrets=True
    )

    with pytest.raises(DomainError) as refused:
        await service(target, SecretBox(b"b" * 32)).plan(HOST, exported, "another passphrase!!")

    assert refused.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_WRONG


async def test_an_export_without_secrets_holds_none_and_needs_no_passphrase(
    source: AsyncEngine,
) -> None:
    exported = await service(source, SecretBox(b"a" * 32)).export(
        HOST, Scope(), None, secrets=False
    )

    assert b"sealed" not in exported
    assert b"secret_hash" not in exported
    assert "seal" not in json.loads(exported)


async def test_an_organisation_export_holds_only_that_organisation(source: AsyncEngine) -> None:
    exported = await service(source, SecretBox(b"a" * 32)).export(
        HOST, Scope("acme"), None, secrets=False
    )

    data = json.loads(exported)
    assert data["scope"] == {"organisation": "acme"}
    assert "platform" not in data
    assert [o["slug"] for o in data["organisations"]] == ["acme"]


async def test_an_organisation_admin_exports_what_they_may_change_and_no_integration_secrets(
    source: AsyncEngine,
) -> None:
    box = SecretBox(b"a" * 32)
    superuser = Actor("root", principal("root", "Root", [Role.SUPERUSER]))
    admin = Actor("org", principal("org", "Org", [Role.ORG_ADMIN], ["acme"]))
    svc = service(source, box)

    everything = json.loads(await svc.export(superuser, Scope("acme"), None, secrets=False))
    own = json.loads(await svc.export(admin, Scope("acme"), None, secrets=False))

    assert [o["slug"] for o in own["organisations"]] == ["acme"]
    assert [g["slug"] for g in own["organisations"][0]["groups"]] == [
        g["slug"] for g in everything["organisations"][0]["groups"]
    ]
    mine, all_of_it = own["organisations"][0], everything["organisations"][0]
    assert set(mine) < set(all_of_it)
    assert {"groups", "holiday_calendars", "origins"} <= set(mine)
    # Branding is held back from the organisation admin (ADR 0039), so a document of theirs
    # cannot carry it: applying it elsewhere would be refused for the same reason.
    assert not {"theme_tokens", "accent_color", "display_name"} & set(mine)


async def test_a_plan_says_whether_the_document_held_sealed_secrets(
    source: AsyncEngine, target: AsyncEngine
) -> None:
    box = SecretBox(b"a" * 32)
    with_secrets = await service(source, box).export(HOST, Scope(), PASSPHRASE, secrets=True)
    without = await service(source, box).export(HOST, Scope(), None, secrets=False)

    sealed = await service(target, SecretBox(b"b" * 32)).plan(HOST, with_secrets, PASSPHRASE)
    plain = await service(target, SecretBox(b"b" * 32)).plan(HOST, without, None)

    assert sealed.sealed
    assert not plain.sealed


async def test_an_export_without_secrets_survives_a_secret_that_cannot_be_opened(
    source: AsyncEngine,
) -> None:
    wrong_key = SecretBox(b"z" * 32)  # the rows were sealed under another key (a rotation)

    exported = await service(source, wrong_key).export(HOST, Scope(), None, secrets=False)

    assert json.loads(exported)["format"] == "openplanner.config/v1"
    assert b"sealed" not in exported
