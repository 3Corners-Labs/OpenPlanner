"""Reading a SQLite package as a document (ADR 0099 point 14): size, rights, audit."""

from datetime import UTC, datetime

import pytest

from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.configuration_changes import ConfigurationSnapshot, RuleContext, Usage
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from app.domain.configuration_package import (
    IMPORT_DETAIL,
    MIN_PASSPHRASE_LENGTH,
    PackageSummary,
)
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.configuration_tree import Entity
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.configuration_package import FakeTransfer
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.principals import principal

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
PASSPHRASE = "x" * MIN_PASSPHRASE_LENGTH
SUMMARY = PackageSummary(1, NOW, "bron", "1.0.0", "abc", {"organisations": 2, "groups": 3}, 4)
HOST = Actor.host()


def organisation() -> Entity:
    fields = {
        "name": "Acme",
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


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    unit = InMemoryUnitOfWork()
    root = Entity("installation", children={"platform": (Entity("platform"),)})
    unit.state.configuration = ConfigurationSnapshot(root, Usage(), 0)
    return unit


@pytest.fixture
def transfer() -> FakeTransfer:
    root = Entity(
        "installation",
        children={"platform": (Entity("platform"),), "organisations": (organisation(),)},
    )
    return FakeTransfer(SUMMARY, root)


@pytest.fixture
def service(uow: InMemoryUnitOfWork, transfer: FakeTransfer) -> ConfigurationPackageService:
    clock = FixedClock(NOW)
    admin = AdminService(uow, clock)
    documents = ConfigurationDocumentService(
        uow,
        clock,
        admin,
        JsonCodec(),
        PassphraseSealing(),
        installation="test",
        app_version="1.0.0",
        context=lambda: RuleContext(),
        locked=frozenset,
        max_bytes=lambda: 100_000,
        max_changes=lambda: 500,
    )
    return ConfigurationPackageService(transfer, documents, clock, admin, max_bytes=lambda: 100)


def test_the_summary_detail_names_counts_only() -> None:
    assert SUMMARY.detail == (
        f"{IMPORT_DETAIL},organisations:2,groups:3,identity_providers:0,api_clients:0,secrets:4"
    )


async def test_import_refuses_an_empty_or_oversized_package(
    service: ConfigurationPackageService,
) -> None:
    with pytest.raises(DomainError) as empty:
        await service.import_(HOST, b"", PASSPHRASE)
    with pytest.raises(DomainError) as large:
        await service.inspect(b"x" * 101, PASSPHRASE)

    assert empty.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID
    assert large.value.code is ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE


async def test_import_applies_the_converted_document_and_audits_the_counts(
    service: ConfigurationPackageService, transfer: FakeTransfer, uow: InMemoryUnitOfWork
) -> None:
    summary = await service.import_(HOST, b"package", PASSPHRASE)

    assert summary is SUMMARY
    assert transfer.converted == [(b"package", PASSPHRASE)]
    assert [c.kind for c in uow.state.applied_configuration[0]] == ["organisation"]
    assert uow.state.audit[-1].detail == SUMMARY.detail
    assert uow.state.audit[-1].outcome is Outcome.SUCCESS


async def test_import_is_refused_for_an_actor_without_the_rights_and_audited(
    service: ConfigurationPackageService, uow: InMemoryUnitOfWork
) -> None:
    nobody = Actor("group", principal("group", "G", [Role.GROUP_ADMIN], groups=["demo"]))

    with pytest.raises(DomainError) as refused:
        await service.import_(nobody, b"package", PASSPHRASE)

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.applied_configuration == []
    assert uow.state.audit[-1].outcome is Outcome.DENIED
    assert uow.state.audit[-1].reason == "not_permitted"
