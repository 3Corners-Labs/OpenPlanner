"""Export, plan and apply of a configuration document, on fakes (ADR 0099 points 6, 11, 12)."""

import json
from datetime import UTC, datetime

import pytest

from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.configuration_changes import ConfigurationSnapshot, RuleContext, Usage
from app.domain.configuration_document import Scope
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.configuration_tree import Entity
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import SUPER, principal

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
HOST = Actor("system:cli")
PASSPHRASE = "a long enough passphrase"  # noqa: S105 - a test passphrase
DOCUMENT = {
    "format": "openplanner.config/v1",
    "organisations": [{"slug": "acme", "name": "Acme", "default_locale": "nl"}],
}


def organisation(name: str = "Acme") -> Entity:
    fields = {
        "name": name,
        "idp_key": None,
        "default_locale": "nl",
        "display_name": None,
        "accent_color": "#01689B",
        "header_style": "standard",
        "theme_tokens": {},
        "contacts": None,
        "origins": [],
        "holiday_calendars": [],
    }
    return Entity("organisation", ("acme",), fields=fields)


def uow(*organisations: Entity, usage: Usage | None = None) -> InMemoryUnitOfWork:
    unit = InMemoryUnitOfWork()
    root = Entity(
        "installation",
        children={"platform": (Entity("platform"),), "organisations": organisations},
    )
    unit.state.configuration = ConfigurationSnapshot(root, usage or Usage(), 0)
    return unit


def build(
    unit: InMemoryUnitOfWork, *, changes: int = 500, issuer_of: object = None
) -> ConfigurationDocumentService:
    clock = FixedClock(NOW)
    return ConfigurationDocumentService(
        unit,
        clock,
        AdminService(unit, clock),
        JsonCodec(),
        PassphraseSealing(),
        installation="test",
        app_version="1.0.0",
        context=lambda: RuleContext(),
        locked=lambda: frozenset(),
        max_bytes=lambda: 100_000,
        max_changes=lambda: changes,
        issuer_of=issuer_of,  # type: ignore[arg-type]
    )


def encoded(data: object) -> bytes:
    return json.dumps(data).encode()


async def test_a_plan_writes_nothing() -> None:
    unit = uow()

    plan = await build(unit).plan(HOST, encoded(DOCUMENT), None)

    assert plan.lines() == ["create organisation acme: name, default_locale"]
    assert unit.state.applied_configuration == []
    assert unit.state.audit == []


async def test_an_apply_writes_the_plan_once_and_audits_every_change_by_key() -> None:
    unit = uow()

    result = await build(unit).apply(HOST, encoded(DOCUMENT), None, None)

    assert result.applied
    assert len(unit.state.applied_configuration) == 1
    assert [c.kind for c in unit.state.applied_configuration[0]] == ["organisation"]
    (event,) = unit.state.audit
    assert event.outcome is Outcome.SUCCESS
    assert event.actor == "system:cli"
    assert event.organisation == "acme"
    assert event.detail.startswith("configuration.apply,plan:")
    assert "action:create,kind:organisation,object:acme" in event.detail
    assert "Acme" not in event.detail


async def test_an_alert_target_is_named_in_the_audit_by_a_digest_never_by_its_address() -> None:
    unit = uow(organisation())
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {"slug": "acme", "alert_targets": {"email": {"ops@voorbeeld.nl": "warning"}}}
        ],
    }

    await build(unit).apply(HOST, encoded(data), None, None)

    (event,) = unit.state.audit
    assert "voorbeeld" not in event.detail
    assert "kind:organisation_alert_target" in event.detail


async def test_a_document_that_changes_nothing_writes_nothing() -> None:
    unit = uow(organisation())

    result = await build(unit).apply(HOST, encoded(DOCUMENT), None, None)

    assert result.plan.changes == ()
    assert result.applied  # an empty plan is applied: the version moves, nothing else
    assert unit.state.audit == []


async def test_a_refused_plan_writes_nothing_and_is_audited_with_its_code() -> None:
    unit = uow()
    data = {"format": "openplanner.config/v1", "organisations": [{"slug": "acme"}]}

    result = await build(unit).apply(HOST, encoded(data), None, None)

    assert not result.applied
    assert result.plan.refusals
    assert unit.state.applied_configuration == []
    (event,) = unit.state.audit
    assert event.outcome is Outcome.DENIED
    assert event.reason == ErrorCode.INVALID_CONFIGURATION.value


async def test_a_plan_with_deletions_needs_the_reviewed_hash() -> None:
    unit = uow(organisation())
    data = {
        "format": "openplanner.config/v1",
        "organisations": [{"slug": "acme", "action": "delete"}],
    }
    service = build(unit)
    plan = await service.plan(HOST, encoded(data), None)

    with pytest.raises(DomainError) as missing:
        await service.apply(HOST, encoded(data), None, None)
    with pytest.raises(DomainError) as wrong:
        await service.apply(HOST, encoded(data), None, "0" * 64)
    result = await service.apply(HOST, encoded(data), None, plan.digest)

    assert missing.value.code is ErrorCode.CONFIGURATION_PLAN_STALE
    assert wrong.value.code is ErrorCode.CONFIGURATION_PLAN_STALE
    assert result.applied
    assert [e.reason for e in unit.state.audit if e.outcome is Outcome.DENIED] == [
        "configuration_plan_stale",
        "configuration_plan_stale",
    ]


async def test_the_hash_of_a_plan_made_before_the_configuration_moved_is_stale() -> None:
    unit = uow()
    service = build(unit)
    plan = await service.plan(HOST, encoded(DOCUMENT), None)
    unit.state.settings_version = 5

    with pytest.raises(DomainError) as error:
        await service.apply(HOST, encoded(DOCUMENT), None, plan.digest)

    assert error.value.code is ErrorCode.CONFIGURATION_PLAN_STALE


async def test_more_changes_than_the_limit_are_refused_unwritten() -> None:
    unit = uow()
    data = {
        "format": "openplanner.config/v1",
        "organisations": [{"slug": f"o{n}", "name": "O"} for n in range(3)],
    }

    with pytest.raises(DomainError) as error:
        await build(unit, changes=2).apply(HOST, encoded(data), None, None)

    assert error.value.code is ErrorCode.CONFIGURATION_TOO_MANY_CHANGES
    assert unit.state.applied_configuration == []


async def test_a_document_of_another_scope_than_the_route_allows_is_refused() -> None:
    unit = uow()

    with pytest.raises(DomainError) as error:
        await build(unit).plan(HOST, encoded(DOCUMENT), None, scope=Scope("acme"))

    assert error.value.code is ErrorCode.CONFIGURATION_OUT_OF_SCOPE


async def test_rights_come_from_the_matrix_for_a_person_and_not_for_the_host() -> None:
    unit = uow()
    nobody = Actor("sub-nobody", principal("sub-nobody", "Nobody"))
    superuser = Actor(SUPER.subject, SUPER)

    refused = await build(unit).plan(nobody, encoded(DOCUMENT), None)
    allowed = await build(unit).plan(superuser, encoded(DOCUMENT), None)

    assert [r.code for r in refused.refusals] == [ErrorCode.NOT_PERMITTED]
    assert allowed.ok


async def test_a_new_identity_provider_gets_its_issuer_before_the_lock() -> None:
    unit = uow(organisation())
    fetched: list[tuple[str, str]] = []

    async def issuer_of(url: str, auth: str) -> str:
        fetched.append((url, auth))
        return "https://idp.voorbeeld.nl"

    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "acme",
                "identity_providers": [
                    {
                        "key": "acme-idp",
                        "name": "Acme",
                        "discovery_url": "https://idp.voorbeeld.nl/.well-known/openid-configuration",
                        "client_id": "openplanner",
                        "client_auth": "private_key_jwt",
                        "entitlement_claim": "groups",
                        "entitlement_prefix": "op",
                    }
                ],
            }
        ],
    }

    result = await build(unit, issuer_of=issuer_of).apply(HOST, encoded(data), None, None)

    assert result.applied, result.plan.refusals
    assert fetched == [
        ("https://idp.voorbeeld.nl/.well-known/openid-configuration", "private_key_jwt")
    ]
    written = {c.kind: c for c in unit.state.applied_configuration[0]}
    assert written["identity_provider"].values["issuer"] == "https://idp.voorbeeld.nl"


async def test_a_provider_without_a_way_to_fetch_discovery_is_a_refusal() -> None:
    unit = uow(organisation())
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {
                "slug": "acme",
                "identity_providers": [
                    {
                        "key": "acme-idp",
                        "name": "Acme",
                        "discovery_url": "https://idp.voorbeeld.nl/.well-known/openid-configuration",
                        "client_id": "openplanner",
                        "client_auth": "private_key_jwt",
                        "entitlement_claim": "groups",
                        "entitlement_prefix": "op",
                    }
                ],
            }
        ],
    }

    result = await build(unit).apply(HOST, encoded(data), None, None)

    assert not result.applied
    assert [r.code for r in result.plan.refusals] == [ErrorCode.INTEGRATION_NOT_READY]


async def test_an_export_is_audited_and_holds_no_secret_when_none_are_wanted() -> None:
    unit = uow(organisation())

    data = await build(unit).export(HOST, Scope(), None, secrets=False)

    document = json.loads(data)
    assert document["format"] == "openplanner.config/v1"
    assert document["meta"]["installation"] == "test"
    assert "seal" not in document
    (event,) = unit.state.audit
    assert event.detail == "configuration.export"
    assert event.outcome is Outcome.SUCCESS


async def test_an_export_with_secrets_needs_a_long_passphrase() -> None:
    with pytest.raises(DomainError) as short:
        await build(uow()).export(HOST, Scope(), "too short", secrets=True)
    with pytest.raises(DomainError) as none:
        await build(uow()).export(HOST, Scope(), None, secrets=True)

    assert short.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_SHORT
    assert none.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_SHORT


async def test_an_organisation_export_keeps_only_that_organisation() -> None:
    other = Entity("organisation", ("other",), fields=organisation().fields)
    unit = uow(organisation(), other)

    data = await build(unit).export(HOST, Scope("acme"), PASSPHRASE, secrets=True)

    document = json.loads(data)
    assert [o["slug"] for o in document["organisations"]] == ["acme"]
    assert document["scope"] == {"organisation": "acme"}
    assert "platform" not in document
    (event,) = unit.state.audit
    assert event.organisation == "acme"
