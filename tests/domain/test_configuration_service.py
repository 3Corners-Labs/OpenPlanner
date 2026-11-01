"""The rules of a configuration package (ADR 0089): passphrase, size, in use, audit."""

from datetime import UTC, datetime

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.configuration_package import (
    EXPORT_DETAIL,
    IMPORT_DETAIL,
    MIN_PASSPHRASE_LENGTH,
    PackageSummary,
    check_passphrase,
)
from app.domain.configuration_service import ConfigurationPackageService
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
PASSPHRASE = "x" * MIN_PASSPHRASE_LENGTH
SUMMARY = PackageSummary(1, NOW, "bron", "1.0.0", "abc", {"organisations": 2, "groups": 3}, 4)


class FakeTransfer:
    """A transfer that records what it was asked and answers from attributes."""

    def __init__(self) -> None:
        self.exported: list[str] = []
        self.imported: list[tuple[bytes, str]] = []
        self.busy = False

    async def export(self, passphrase: str) -> bytes:
        self.exported.append(passphrase)
        return b"SQLite format 3\x00package"

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        return SUMMARY

    async def import_(self, package: bytes, passphrase: str) -> PackageSummary:
        self.imported.append((package, passphrase))
        return SUMMARY

    async def in_use(self) -> bool:
        return self.busy


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


@pytest.fixture
def transfer() -> FakeTransfer:
    return FakeTransfer()


@pytest.fixture
def service(uow: InMemoryUnitOfWork, transfer: FakeTransfer) -> ConfigurationPackageService:
    clock = FixedClock(NOW)
    return ConfigurationPackageService(
        transfer, clock, AdminService(uow, clock), max_bytes=lambda: 100
    )


def test_a_short_passphrase_is_refused() -> None:
    with pytest.raises(DomainError) as refused:
        check_passphrase("x" * (MIN_PASSPHRASE_LENGTH - 1))

    assert refused.value.code is ErrorCode.CONFIGURATION_PASSPHRASE_SHORT
    check_passphrase(PASSPHRASE)


def test_the_summary_detail_names_counts_only() -> None:
    assert SUMMARY.detail == (
        f"{IMPORT_DETAIL},organisations:2,groups:3,identity_providers:0,api_clients:0,secrets:4"
    )


async def test_export_is_audited_and_never_carries_the_passphrase(
    service: ConfigurationPackageService, transfer: FakeTransfer, uow: InMemoryUnitOfWork
) -> None:
    package = await service.export("sub-super", PASSPHRASE)

    assert package.startswith(b"SQLite")
    assert transfer.exported == [PASSPHRASE]
    event = uow.state.audit[-1]
    assert (event.actor, event.outcome, event.detail) == (
        "sub-super",
        Outcome.SUCCESS,
        EXPORT_DETAIL,
    )
    assert PASSPHRASE not in event.detail + event.reason


async def test_a_refused_export_is_audited_with_its_code(
    service: ConfigurationPackageService, transfer: FakeTransfer, uow: InMemoryUnitOfWork
) -> None:
    with pytest.raises(DomainError):
        await service.export("sub-super", "short")

    assert transfer.exported == []
    event = uow.state.audit[-1]
    assert (event.outcome, event.reason) == (Outcome.DENIED, "configuration_passphrase_short")


async def test_import_is_refused_while_the_installation_is_in_use(
    service: ConfigurationPackageService, transfer: FakeTransfer, uow: InMemoryUnitOfWork
) -> None:
    transfer.busy = True

    with pytest.raises(DomainError) as refused:
        await service.import_("sub-super", b"package", PASSPHRASE)

    assert refused.value.code is ErrorCode.CONFIGURATION_INSTALLATION_IN_USE
    assert transfer.imported == []
    assert uow.state.audit[-1].reason == "configuration_installation_in_use"


async def test_import_refuses_an_empty_or_oversized_package(
    service: ConfigurationPackageService,
) -> None:
    with pytest.raises(DomainError) as empty:
        await service.import_("sub-super", b"", PASSPHRASE)
    with pytest.raises(DomainError) as large:
        await service.inspect(b"x" * 101, PASSPHRASE)

    assert empty.value.code is ErrorCode.CONFIGURATION_PACKAGE_INVALID
    assert large.value.code is ErrorCode.CONFIGURATION_PACKAGE_TOO_LARGE


async def test_import_is_audited_with_the_counts(
    service: ConfigurationPackageService, transfer: FakeTransfer, uow: InMemoryUnitOfWork
) -> None:
    summary = await service.import_("sub-super", b"package", PASSPHRASE)

    assert summary is SUMMARY
    assert transfer.imported == [(b"package", PASSPHRASE)]
    assert uow.state.audit[-1].detail == SUMMARY.detail
    assert uow.state.audit[-1].outcome is Outcome.SUCCESS
