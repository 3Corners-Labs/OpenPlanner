"""Export, plan and apply of a configuration document, on fakes (ADR 0099 points 6, 11, 12)."""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Permission, Role, Target
from app.domain.configuration_changes import ConfigurationSnapshot, RuleContext, Usage
from app.domain.configuration_document import Scope
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.configuration_tree import Entity
from app.domain.configuration_values import Secret
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import SUPER, principal

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
HOST = Actor.host()
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
    unit: InMemoryUnitOfWork,
    *,
    changes: int = 500,
    issuer_of: object = None,
    settings: dict[str, object] | None = None,
    plaintext: bool = False,
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
        context=lambda: RuleContext(settings=settings or {}),
        locked=lambda: frozenset(),
        max_bytes=lambda: 100_000,
        max_changes=lambda: changes,
        issuer_of=issuer_of,  # type: ignore[arg-type]
        plaintext_export=lambda: plaintext,
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
    assert result.applied
    assert unit.state.applied_configuration == []  # nothing is written, the version stays
    assert unit.state.settings_version == 0
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


async def test_an_export_with_secrets_is_refused_when_the_installation_cannot_open_them() -> None:
    unit = uow()
    snapshot = unit.state.configuration
    assert snapshot is not None
    unit.state.configuration = replace(snapshot, secrets_available=False)

    with pytest.raises(DomainError) as error:
        await build(unit).export(HOST, Scope(), PASSPHRASE, secrets=True)

    assert error.value.code is ErrorCode.INTEGRATION_NOT_READY
    assert await build(unit).export(HOST, Scope(), None, secrets=False)


async def test_a_limit_the_portal_changed_beats_the_process_default() -> None:
    unit = uow(organisation())
    snapshot = unit.state.configuration
    assert snapshot is not None
    unit.state.configuration = replace(snapshot, settings={"embedding_max_origins": 1})
    data = {
        "format": "openplanner.config/v1",
        "organisations": [
            {"slug": "acme", "origins": ["https://a.voorbeeld.nl", "https://b.voorbeeld.nl"]}
        ],
    }

    plan = await build(unit, settings={"embedding_max_origins": 20}).plan(HOST, encoded(data), None)

    assert not plan.ok


async def test_an_export_needs_the_manage_permission_of_its_scope_and_secrets_the_platform_s() -> (
    None
):
    nobody = Actor("sub-nobody", principal("sub-nobody", "Nobody"))
    org_admin = Actor("s-org", principal("s-org", "Org", [Role.ORG_ADMIN], ["acme"]))
    superuser = Actor(SUPER.subject, SUPER)
    unit = uow(organisation())

    for actor, scope, secrets in (
        (nobody, Scope("acme"), False),
        (nobody, Scope(), False),
        (org_admin, Scope(), False),
        (org_admin, Scope("acme"), True),
    ):
        with pytest.raises(DomainError) as refused:
            await build(unit).export(actor, scope, PASSPHRASE if secrets else None, secrets=secrets)
        assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert await build(unit).export(org_admin, Scope("acme"), None, secrets=False)
    assert await build(unit).export(superuser, Scope(), PASSPHRASE, secrets=True)


async def test_a_wrong_hash_is_refused_before_any_discovery_is_fetched() -> None:
    unit = uow(organisation())
    fetched: list[str] = []

    async def issuer_of(url: str, auth: str) -> str:
        fetched.append(url)
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

    with pytest.raises(DomainError) as error:
        await build(unit, issuer_of=issuer_of).apply(HOST, encoded(data), None, "0" * 64)

    assert error.value.code is ErrorCode.CONFIGURATION_PLAN_STALE
    assert fetched == []


async def test_a_writer_failing_inside_the_lock_is_audited_with_its_code() -> None:
    unit = uow()

    async def broken(changes: object) -> int:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "kind")

    original = type(unit).locked

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def locked(self: InMemoryUnitOfWork):  # type: ignore[no-untyped-def]
        async with original(self) as tx:
            tx.apply_configuration = broken  # type: ignore[method-assign,assignment]
            yield tx

    unit.locked = locked.__get__(unit)  # type: ignore[method-assign]

    with pytest.raises(DomainError):
        await build(unit).apply(HOST, encoded(DOCUMENT), None, None)

    (event,) = unit.state.audit
    assert event.outcome is Outcome.DENIED
    assert event.reason == ErrorCode.INVALID_CONFIGURATION.value


def test_an_actor_without_a_principal_is_the_host_and_only_by_asking() -> None:
    assert Actor.host().principal is None
    assert Actor.host().may_change(Permission.ROLES_MANAGE, Target.everything())
    with pytest.raises(TypeError):
        Actor("sub-x")  # type: ignore[call-arg]


def with_secret() -> InMemoryUnitOfWork:
    """A unit whose platform holds one secret, opened in memory as a snapshot has it."""
    unit = uow()
    snapshot = unit.state.configuration
    assert snapshot is not None
    secret = Entity("platform_secret", ("smtp",), fields={"secret": Secret("hunter2")})
    platform = Entity("platform", children={"secrets": (secret,)})
    root = replace(snapshot.root, children={**snapshot.root.children, "platform": (platform,)})
    unit.state.configuration = replace(snapshot, root=root)
    return unit


async def test_a_plain_export_holds_the_secret_in_clear_text_without_a_passphrase() -> None:
    unit = with_secret()

    data = await build(unit, plaintext=True).export(HOST, Scope(), None, secrets=True, plain=True)

    document = json.loads(data)
    assert document["platform"]["secrets"] == {"smtp": {"plain": "hunter2"}}
    assert "seal" not in document
    (event,) = unit.state.audit
    assert event.detail == "configuration.export,secrets,plain"
    assert "hunter2" not in repr(event)


async def test_a_plain_export_is_refused_while_the_host_has_not_allowed_it() -> None:
    unit = with_secret()

    with pytest.raises(DomainError) as error:
        await build(unit).export(HOST, Scope(), None, secrets=True, plain=True)

    assert error.value.code is ErrorCode.CONFIGURATION_PLAINTEXT_DISABLED
    assert unit.state.audit == []


async def test_a_plain_export_needs_the_platform_manage_permission_like_a_sealed_one() -> None:
    org_admin = Actor("s-org", principal("s-org", "Org", [Role.ORG_ADMIN], ["acme"]))

    with pytest.raises(DomainError) as error:
        await build(uow(organisation()), plaintext=True).export(
            org_admin, Scope("acme"), None, secrets=True, plain=True
        )

    assert error.value.code is ErrorCode.NOT_PERMITTED


async def test_a_plain_export_applies_again_and_changes_nothing_the_second_time() -> None:
    unit = with_secret()
    service = build(unit, plaintext=True)
    data = await service.export(HOST, Scope(), None, secrets=True, plain=True)

    plan = await service.plan(HOST, data, None)

    assert plan.ok
    assert not plan.sealed
    assert plan.changes == ()
