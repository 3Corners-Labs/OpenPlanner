"""Host tools for one organisation identity provider (ADR 0083 point 19)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.db.engine import create_engine
from app.adapters.db.models import AuditEventRow
from app.adapters.db.uow import SqlUnitOfWork
from app.cli import _idp_check, _idp_disable, main
from app.config import Settings
from app.domain.identity_providers import ClientAuth, OrganisationProvider
from app.domain.login_domains import DomainClaim, DomainState
from tests.adapters.db.conftest import seed_group
from tests.adapters.test_oidc_check import DISCOVERY, _transport
from tests.support.database import migrated_database

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _public(host: str, port: int) -> list[str]:
    return ["93.184.215.14"]


def _private(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    found = settings.model_copy(
        update={
            "database_url": migrated_database(tmp_path, monkeypatch, "cli-idp"),
            "environment": "prod",
        }
    )
    asyncio.run(_seed(found))
    return found


async def _seed(found: Settings) -> None:
    engine = create_engine(found.database_url)
    try:
        await seed_group(engine, slug="aangifte")
        uow = SqlUnitOfWork(engine, None)
        async with uow.locked() as tx:
            await tx.add_identity_provider(
                OrganisationProvider(
                    None,
                    "org-aangifte",
                    "gem-a",
                    "Gemeente A",
                    DISCOVERY,
                    "https://idp.example.nl",
                    "openplanner",
                    ClientAuth.CLIENT_SECRET_BASIC,
                    "openid profile email",
                    "openplanner_entitlements",
                    "openplanner",
                    True,
                    False,
                    NOW,
                    NOW,
                ),
                None,
            )
            await tx.add_login_domain(
                DomainClaim(None, "gem-a", "gemeente-a.nl", "tok", DomainState.VERIFIED, NOW)
            )
    finally:
        await engine.dispose()


async def test_the_check_reads_discovery_and_keys_and_lists_the_domains(
    configured: Settings,
) -> None:
    seen: list[httpx.Request] = []

    report, ok = await _idp_check(configured, "gem-a", _transport(seen), _public)

    assert "OK    outbound guard" in report
    assert "OK    discovery" in report
    assert (
        "gem-a of organisation org-aangifte: on, client authentication client_secret_basic"
        in report
    )
    assert "  gemeente-a.nl  verified" in report
    assert "FAIL  client secret" in report  # no secret stored: the check says so
    assert not ok
    assert all(request.method == "GET" for request in seen)


async def test_the_check_shows_the_guard_refusing_a_private_address(
    configured: Settings,
) -> None:
    """B8: the provider's address resolves to a private network."""
    report, ok = await _idp_check(configured, "gem-a", _transport([]), _private)

    assert "FAIL  outbound guard" in report
    assert not ok


async def test_an_unknown_key_is_said(configured: Settings) -> None:
    report, ok = await _idp_check(configured, "nope", _transport([]), _public)

    assert (report, ok) == ("no identity provider with key 'nope'\n", False)


async def test_disable_switches_the_provider_off_audited_as_the_cli(configured: Settings) -> None:
    report, ok = await _idp_disable(configured, "gem-a")
    again, _ = await _idp_disable(configured, "gem-a")

    engine = create_engine(configured.database_url)
    try:
        async with engine.connect() as connection:
            audit = (await connection.execute(select(AuditEventRow))).all()
        uow = SqlUnitOfWork(engine, None)
        async with uow.read() as tx:
            provider = await tx.get_identity_provider("gem-a")
    finally:
        await engine.dispose()
    assert (report, ok) == ("switched off gem-a\n", True)
    assert again == "gem-a was already off\n"
    assert provider is not None
    assert not provider.enabled
    assert [(a.actor, a.action) for a in audit if a.action.startswith("idp.")] == [
        ("system:cli", "idp.disabled")
    ]


def test_the_commands_are_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_check(settings: Settings, key: str) -> tuple[str, bool]:
        calls.append(("check", key))
        return "", True

    async def fake_disable(settings: Settings, key: str) -> tuple[str, bool]:
        calls.append(("disable", key))
        return "", True

    monkeypatch.setattr("app.cli._idp_check", fake_check)
    monkeypatch.setattr("app.cli._idp_disable", fake_disable)
    monkeypatch.setattr("app.cli.Settings", lambda: None)

    assert main(["oidc-check", "--idp", "gem-a"]) == 0
    assert main(["idp", "disable", "gem-a"]) == 0
    assert calls == [("check", "gem-a"), ("disable", "gem-a")]
