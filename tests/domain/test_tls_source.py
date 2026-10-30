"""The certificate source of the bundled proxy, managed in the portal (ADR 0066)."""

from collections.abc import Mapping
from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import (
    MAX_PEM_BYTES,
    OwnCertificate,
    PemPair,
    fingerprint_text,
    validate,
)
from app.domain.settings_service import SettingsService
from app.domain.system_log import SystemLog
from app.domain.tls import Certificate, Finding, ProbeResult, Trust
from app.domain.tls_check import TlsWatch
from app.domain.tls_source import CertificateSourceService
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.fakes.tls import FakeCertificateDelivery, FakeCertificateProbe, FakePemReader
from tests.support.domain import DEMO_ORG_ADMIN, DEMO_SUPERUSER, NOW

HOST = "afspraak.example.nl"
FINGERPRINT = "ab" * 32
CHAIN = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n"


def certificate(
    *, days_left: int = 60, days_valid: int = 30, names: tuple[str, ...] = (HOST,)
) -> Certificate:
    return Certificate(
        HOST,
        "Staat der Nederlanden (Organisatie CA)",
        NOW - timedelta(days=days_valid),
        NOW + timedelta(days=days_left),
        names,
    )


def pair(cert: Certificate | None = None, *, key_matches: bool = True) -> PemPair:
    return PemPair(cert or certificate(), FINGERPRINT, CHAIN, KEY, key_matches)


class Harness:
    """The service with every collaborator a test may inspect or steer."""

    def __init__(self, *, bundled: bool = True, source: str = "acme") -> None:
        self.uow = InMemoryUnitOfWork()
        self.clock = FixedClock(NOW)
        self.reader = FakePemReader(pair())
        self.delivery = FakeCertificateDelivery()
        self.probe = FakeCertificateProbe()
        self.source = source
        self.bundled = bundled
        self.locked: frozenset[str] = frozenset()
        admin = AdminService(self.uow, self.clock)

        def combination(_: Mapping[str, object]) -> str | None:
            return None

        settings = SettingsService(
            self.uow, self.clock, admin, locked=lambda: self.locked, combination=combination
        )
        watch = TlsWatch(
            self.uow,
            self.clock,
            self.probe,
            SystemLog(self.uow, self.clock),
            public_base_url=f"https://{HOST}",
            interval=timedelta(hours=6),
            warn_within=timedelta(days=20),
            repeat=timedelta(hours=24),
        )
        self.service = CertificateSourceService(
            self.uow,
            self.clock,
            admin,
            settings,
            self.reader,
            self.delivery,
            watch,
            host=lambda: HOST,
            bundled=lambda: self.bundled,
            source=lambda: self.source,
        )

    def audit_details(self) -> list[str]:
        return [e.detail for e in self.uow.state.audit if e.outcome is Outcome.SUCCESS]

    def refusals(self) -> list[str]:
        return [e.reason for e in self.uow.state.audit if e.outcome is Outcome.FAILURE]


@pytest.fixture
def harness() -> Harness:
    return Harness()


# --- the rules of an upload ---------------------------------------------------------------


def test_a_matching_valid_certificate_for_the_host_passes() -> None:
    validate(pair(), HOST, NOW)


@pytest.mark.parametrize(
    ("candidate", "code", "field"),
    [
        (pair(key_matches=False), ErrorCode.TLS_KEY_MISMATCH, "key"),
        (pair(certificate(days_left=0)), ErrorCode.TLS_CERTIFICATE_EXPIRED, "chain"),
        (pair(certificate(days_valid=-1)), ErrorCode.TLS_CERTIFICATE_NOT_YET_VALID, "chain"),
        (pair(certificate(names=("other.example.nl",))), ErrorCode.TLS_NAME_MISMATCH, "chain"),
    ],
)
def test_a_pair_the_proxy_could_not_serve_is_refused(
    candidate: PemPair, code: ErrorCode, field: str
) -> None:
    with pytest.raises(DomainError) as refused:
        validate(candidate, HOST, NOW)

    assert refused.value.code is code
    assert refused.value.field == field


def test_a_wildcard_covers_the_host_and_an_unknown_host_is_not_checked() -> None:
    validate(pair(certificate(names=("*.example.nl",))), HOST, NOW)
    validate(pair(certificate(names=("other.example.nl",))), None, NOW)


def test_the_key_never_appears_in_a_repr() -> None:
    assert KEY not in repr(pair())
    assert "key_pem" not in repr(pair())


def test_a_fingerprint_is_shown_as_browsers_show_it() -> None:
    assert fingerprint_text("abcdef01") == "AB:CD:EF:01"
    assert OwnCertificate(certificate(), "0a1b", CHAIN, NOW).fingerprint_text() == "0A:1B"


# --- uploading, replacing and removing ---------------------------------------------------


async def test_the_superuser_uploads_a_pair_which_is_stored_sealed_and_audited(
    harness: Harness,
) -> None:
    stored = await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    assert stored.fingerprint == FINGERPRINT
    assert stored.uploaded_at == NOW
    assert harness.uow.state.own_certificate == stored
    assert harness.uow.state.own_certificate_key == KEY
    assert harness.audit_details() == [f"tls.certificate.upload:{FINGERPRINT}:2026-03-02"]
    assert harness.delivery.calls == []  # the source is still acme


async def test_an_upload_while_the_source_is_own_reaches_the_proxy_at_once(
    harness: Harness,
) -> None:
    harness.source = "own"

    await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    assert harness.delivery.calls == [f"publish:{FINGERPRINT}"]


async def test_an_unreadable_upload_is_refused_and_audited_without_content(
    harness: Harness,
) -> None:
    harness.reader.unreadable = True

    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_SUPERUSER, b"not a certificate", b"nor a key")

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_UNREADABLE
    assert harness.refusals() == ["tls_certificate_unreadable"]
    assert harness.uow.state.own_certificate is None
    assert "not a certificate" not in "".join(e.detail for e in harness.uow.state.audit)


async def test_an_oversized_file_is_refused_before_it_is_parsed(harness: Harness) -> None:
    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_SUPERUSER, b"x" * (MAX_PEM_BYTES + 1), b"key")

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_UNREADABLE
    assert harness.reader.read_sizes == []


async def test_a_key_that_does_not_fit_is_refused(harness: Harness) -> None:
    harness.reader.pair = pair(key_matches=False)

    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    assert refused.value.code is ErrorCode.TLS_KEY_MISMATCH
    assert harness.uow.state.own_certificate is None


async def test_without_a_settings_key_nothing_is_stored(harness: Harness) -> None:
    harness.uow.state.secrets_available = False

    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    assert refused.value.code is ErrorCode.SETTING_NO_KEY


async def test_an_org_admin_may_not_upload(harness: Harness) -> None:
    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_ORG_ADMIN, b"chain", b"key")

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert harness.uow.state.own_certificate is None


async def test_removing_is_refused_while_the_pair_is_in_use(harness: Harness) -> None:
    await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")
    harness.source = "own"

    with pytest.raises(DomainError) as refused:
        await harness.service.remove(DEMO_SUPERUSER)

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_IN_USE
    assert harness.uow.state.own_certificate is not None


async def test_removing_with_acme_deletes_the_pair_and_withdraws_it(harness: Harness) -> None:
    await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    assert await harness.service.remove(DEMO_SUPERUSER) is True
    assert harness.uow.state.own_certificate is None
    assert harness.uow.state.own_certificate_key is None
    assert harness.delivery.calls == ["withdraw"]
    assert harness.audit_details()[-1] == f"tls.certificate.remove:{FINGERPRINT}:2026-03-02"
    assert await harness.service.remove(DEMO_SUPERUSER) is False


# --- choosing the source -----------------------------------------------------------------


async def test_own_needs_an_uploaded_pair(harness: Harness) -> None:
    with pytest.raises(DomainError) as refused:
        await harness.service.choose(DEMO_SUPERUSER, "own")

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_MISSING
    assert harness.uow.state.platform_settings == {}
    assert harness.refusals() == ["tls_certificate_missing"]


async def test_choosing_own_stores_the_setting_and_publishes_the_pair(harness: Harness) -> None:
    await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    await harness.service.choose(DEMO_SUPERUSER, " Own ")

    assert harness.uow.state.platform_settings == {"tls_certificate_source": "own"}
    assert harness.delivery.published == FINGERPRINT
    assert any("set:tls_certificate_source:acme->own" in d for d in harness.audit_details())


async def test_choosing_acme_again_withdraws_the_pair_and_keeps_it_stored(
    harness: Harness,
) -> None:
    await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")
    await harness.service.choose(DEMO_SUPERUSER, "own")

    await harness.service.choose(DEMO_SUPERUSER, "acme")

    assert harness.delivery.published is None
    assert harness.uow.state.own_certificate is not None


async def test_a_value_outside_the_choices_is_refused(harness: Harness) -> None:
    with pytest.raises(DomainError) as refused:
        await harness.service.choose(DEMO_SUPERUSER, "files")

    assert refused.value.code is ErrorCode.SETTING_INVALID


async def test_a_locked_source_cannot_be_changed_in_the_portal(harness: Harness) -> None:
    harness.locked = frozenset({"tls_certificate_source"})

    with pytest.raises(DomainError) as refused:
        await harness.service.choose(DEMO_SUPERUSER, "acme")

    assert refused.value.code is ErrorCode.SETTING_LOCKED


async def test_without_the_bundled_proxy_nothing_applies(harness: Harness) -> None:
    harness.bundled = False

    assert harness.service.applicable() is False
    for call in (
        harness.service.upload(DEMO_SUPERUSER, b"chain", b"key"),
        harness.service.remove(DEMO_SUPERUSER),
        harness.service.choose(DEMO_SUPERUSER, "acme"),
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert refused.value.code is ErrorCode.TLS_SOURCE_NOT_APPLICABLE


# --- keeping the delivery in step --------------------------------------------------------


async def test_sync_follows_the_effective_source(harness: Harness) -> None:
    assert await harness.service.sync() == "withdrawn"

    await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")
    harness.source = "own"
    assert await harness.service.sync() == "published"
    assert harness.delivery.published == FINGERPRINT

    harness.uow.state.own_certificate = None  # own without a pair: the proxy falls back
    assert await harness.service.sync() == "withdrawn"


async def test_check_now_probes_and_stores_at_once(harness: Harness) -> None:
    harness.probe.result = ProbeResult(certificate(), Trust.TRUSTED)

    check = await harness.service.check_now(DEMO_SUPERUSER)

    assert check.finding is Finding.OK
    assert harness.uow.state.certificate_check == check
    assert harness.probe.asked == [(HOST, 443)]


async def test_check_now_is_the_superusers(harness: Harness) -> None:
    with pytest.raises(DomainError):
        await harness.service.check_now(DEMO_ORG_ADMIN)

    assert harness.probe.asked == []
