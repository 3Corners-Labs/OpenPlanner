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
    ParsedChain,
    ParsedKey,
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
from tests.fakes.tls import FakeCertificateProbe, FakeCertificateVolume, FakePemReader
from tests.support.domain import DEMO_ORG_ADMIN, DEMO_SUPERUSER, NOW

HOST = "afspraak.example.nl"
FINGERPRINT = "ab" * 32
CHAIN = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n"
PUBLIC = b"public-1"


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


def chain(cert: Certificate | None = None, *, public_key: bytes = PUBLIC) -> ParsedChain:
    return ParsedChain(cert or certificate(), FINGERPRINT, CHAIN, public_key)


class Harness:
    """The service with every collaborator a test may inspect or steer."""

    def __init__(self, *, bundled: bool = True, source: str = "acme") -> None:
        self.uow = InMemoryUnitOfWork()
        self.clock = FixedClock(NOW)
        self.reader = FakePemReader(chain(), ParsedKey(KEY, b"imported"))
        self.volume = FakeCertificateVolume()
        self.probe = FakeCertificateProbe()
        self.source = source
        self.bundled = bundled
        self.host: str | None = HOST
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
            self.volume,
            watch,
            host=lambda: self.host,
            bundled=lambda: self.bundled,
            source=lambda: self.source,
        )

    def audit_details(self) -> list[str]:
        return [e.detail for e in self.uow.state.audit if e.outcome is Outcome.SUCCESS]

    def refusals(self) -> list[str]:
        return [e.reason for e in self.uow.state.audit if e.outcome is Outcome.FAILURE]

    async def request_and_upload(self) -> OwnCertificate:
        """The default path: a request on this host, then the chain the CA issued for it."""
        await self.service.generate_csr(DEMO_SUPERUSER)
        return await self.service.upload(DEMO_SUPERUSER, b"chain")


@pytest.fixture
def harness() -> Harness:
    return Harness()


# --- the rules of a chain ----------------------------------------------------------------


def test_a_valid_certificate_for_the_host_passes() -> None:
    validate(chain(), HOST, NOW)


@pytest.mark.parametrize(
    ("candidate", "code"),
    [
        (chain(certificate(days_left=0)), ErrorCode.TLS_CERTIFICATE_EXPIRED),
        (chain(certificate(days_valid=-1)), ErrorCode.TLS_CERTIFICATE_NOT_YET_VALID),
        (chain(certificate(names=("other.example.nl",))), ErrorCode.TLS_NAME_MISMATCH),
    ],
)
def test_a_chain_the_proxy_could_not_serve_is_refused(
    candidate: ParsedChain, code: ErrorCode
) -> None:
    with pytest.raises(DomainError) as refused:
        validate(candidate, HOST, NOW)

    assert refused.value.code is code
    assert refused.value.field == "chain"


def test_a_wildcard_covers_the_host_and_an_unknown_host_is_not_checked() -> None:
    validate(chain(certificate(names=("*.example.nl",))), HOST, NOW)
    validate(chain(certificate(names=("other.example.nl",))), None, NOW)


def test_keys_never_appear_in_a_repr() -> None:
    assert KEY not in repr(ParsedKey(KEY, PUBLIC))
    assert "public-1" not in repr(chain())
    assert "key_pem" not in repr(ParsedKey(KEY, PUBLIC))


def test_a_fingerprint_is_shown_as_browsers_show_it() -> None:
    assert fingerprint_text("abcdef01") == "AB:CD:EF:01"
    assert OwnCertificate(certificate(), "0a1b", CHAIN, PUBLIC, NOW).fingerprint_text() == "0A:1B"


# --- the default path: a key on this host, a request, the chain ---------------------------


async def test_the_superuser_makes_a_request_and_gets_the_csr(harness: Harness) -> None:
    csr = await harness.service.generate_csr(DEMO_SUPERUSER)

    assert csr.startswith("-----BEGIN CERTIFICATE REQUEST-----")
    assert harness.volume.calls == [f"generate:{HOST}"]
    assert (await harness.service.status()).pending_csr == csr
    assert harness.audit_details() == ["tls.key.generate"]


async def test_the_chain_for_the_request_promotes_the_key_and_is_stored(
    harness: Harness,
) -> None:
    stored = await harness.request_and_upload()

    assert stored.fingerprint == FINGERPRINT
    assert stored.public_key == PUBLIC
    assert harness.uow.state.own_certificate == stored
    assert harness.volume.current == PUBLIC
    assert harness.volume.pending is None
    status = await harness.service.status()
    assert status.key_on_host
    assert status.usable
    assert not status.key_missing
    assert harness.audit_details()[-1] == f"tls.certificate.upload:{FINGERPRINT}:2026-03-02"
    assert harness.volume.published is None  # the source is still acme


async def test_a_chain_for_the_served_key_renews_without_a_new_request(
    harness: Harness,
) -> None:
    await harness.request_and_upload()
    harness.source = "own"
    harness.reader.chain = chain(certificate(days_left=365))

    await harness.service.upload(DEMO_SUPERUSER, b"chain")

    assert harness.volume.calls[-1] == f"publish:{FINGERPRINT}"
    assert harness.volume.current == PUBLIC


async def test_a_chain_for_no_key_on_this_host_is_refused(harness: Harness) -> None:
    await harness.service.generate_csr(DEMO_SUPERUSER)
    harness.reader.chain = chain(public_key=b"somebody else's key")

    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_SUPERUSER, b"chain")

    assert refused.value.code is ErrorCode.TLS_KEY_MISMATCH
    assert refused.value.field == "chain"
    assert harness.uow.state.own_certificate is None
    assert harness.volume.pending == PUBLIC  # the request is still waiting


async def test_a_pending_request_never_disturbs_the_served_pair(harness: Harness) -> None:
    await harness.request_and_upload()
    harness.source = "own"

    await harness.service.generate_csr(DEMO_SUPERUSER)

    assert harness.volume.current == PUBLIC
    assert harness.volume.pending == b"public-2"
    assert "withdraw" not in harness.volume.calls


async def test_a_request_needs_the_bundled_proxy_and_an_https_address(
    harness: Harness,
) -> None:
    harness.host = None

    with pytest.raises(DomainError) as refused:
        await harness.service.generate_csr(DEMO_SUPERUSER)

    assert refused.value.code is ErrorCode.TLS_SOURCE_NOT_APPLICABLE
    assert harness.volume.calls == []


async def test_a_volume_that_cannot_be_written_is_reported(harness: Harness) -> None:
    harness.volume.unwritable = True

    with pytest.raises(DomainError) as refused:
        await harness.service.generate_csr(DEMO_SUPERUSER)

    assert refused.value.code is ErrorCode.TLS_VOLUME_UNWRITABLE
    assert harness.refusals() == ["tls_volume_unwritable"]


# --- importing an existing certificate with its key ---------------------------------------


async def test_an_imported_pair_goes_to_the_volume_and_the_chain_to_the_store(
    harness: Harness,
) -> None:
    harness.reader.chain = chain(public_key=b"imported")

    stored = await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    assert harness.volume.current_pem == KEY
    assert harness.volume.calls == ["import"]
    assert harness.uow.state.own_certificate == stored
    assert harness.audit_details() == [
        f"tls.certificate.upload:{FINGERPRINT}:2026-03-02",
        f"tls.key.import:{FINGERPRINT}:2026-03-02",
    ]
    assert all(KEY not in d for d in harness.audit_details())


async def test_an_imported_key_that_does_not_fit_the_chain_is_refused(harness: Harness) -> None:
    harness.reader.chain = chain(public_key=b"another")

    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_SUPERUSER, b"chain", b"key")

    assert refused.value.code is ErrorCode.TLS_KEY_MISMATCH
    assert refused.value.field == "key"
    assert harness.volume.calls == []
    assert harness.uow.state.own_certificate is None


async def test_an_unreadable_upload_is_refused_and_audited_without_content(
    harness: Harness,
) -> None:
    harness.reader.unreadable = True

    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_SUPERUSER, b"not a certificate")

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_UNREADABLE
    assert harness.refusals() == ["tls_certificate_unreadable"]
    assert "not a certificate" not in "".join(e.detail for e in harness.uow.state.audit)


async def test_an_oversized_file_is_refused_before_it_is_parsed(harness: Harness) -> None:
    with pytest.raises(DomainError):
        await harness.service.upload(DEMO_SUPERUSER, b"x" * (MAX_PEM_BYTES + 1))
    with pytest.raises(DomainError):
        await harness.service.upload(DEMO_SUPERUSER, b"chain", b"x" * (MAX_PEM_BYTES + 1))

    assert harness.reader.read_sizes == []


async def test_an_org_admin_may_not_upload_or_request(harness: Harness) -> None:
    with pytest.raises(DomainError) as refused:
        await harness.service.upload(DEMO_ORG_ADMIN, b"chain")
    with pytest.raises(DomainError):
        await harness.service.generate_csr(DEMO_ORG_ADMIN)

    assert refused.value.code is ErrorCode.NOT_PERMITTED
    assert harness.volume.calls == []


# --- removing -------------------------------------------------------------------------------


async def test_removing_is_refused_while_the_chain_is_in_use(harness: Harness) -> None:
    await harness.request_and_upload()
    harness.source = "own"

    with pytest.raises(DomainError) as refused:
        await harness.service.remove(DEMO_SUPERUSER)

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_IN_USE
    assert harness.uow.state.own_certificate is not None


async def test_removing_with_acme_deletes_the_chain_and_keeps_the_key(harness: Harness) -> None:
    await harness.request_and_upload()

    assert await harness.service.remove(DEMO_SUPERUSER) is True
    assert harness.uow.state.own_certificate is None
    assert harness.volume.current == PUBLIC  # the key on this host is not touched
    assert harness.volume.calls[-1] == "withdraw"
    assert harness.audit_details()[-1] == f"tls.certificate.remove:{FINGERPRINT}:2026-03-02"
    assert await harness.service.remove(DEMO_SUPERUSER) is False


# --- choosing the source -----------------------------------------------------------------


async def test_own_needs_a_stored_chain(harness: Harness) -> None:
    with pytest.raises(DomainError) as refused:
        await harness.service.choose(DEMO_SUPERUSER, "own")

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_MISSING
    assert harness.uow.state.platform_settings == {}
    assert harness.refusals() == ["tls_certificate_missing"]


async def test_own_needs_the_key_on_this_host(harness: Harness) -> None:
    await harness.request_and_upload()
    harness.volume.current = None  # the database was restored on another host

    with pytest.raises(DomainError) as refused:
        await harness.service.choose(DEMO_SUPERUSER, "own")

    assert refused.value.code is ErrorCode.TLS_KEY_MISSING
    assert (await harness.service.status()).key_missing


async def test_choosing_own_stores_the_setting_and_publishes_the_chain(harness: Harness) -> None:
    await harness.request_and_upload()

    await harness.service.choose(DEMO_SUPERUSER, " Own ")

    assert harness.uow.state.platform_settings == {"tls_certificate_source": "own"}
    assert harness.volume.published == FINGERPRINT
    assert any("set:tls_certificate_source:acme->own" in d for d in harness.audit_details())


async def test_choosing_acme_again_withdraws_the_chain_and_keeps_it_stored(
    harness: Harness,
) -> None:
    await harness.request_and_upload()
    await harness.service.choose(DEMO_SUPERUSER, "own")

    await harness.service.choose(DEMO_SUPERUSER, "acme")

    assert harness.volume.published is None
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
        harness.service.generate_csr(DEMO_SUPERUSER),
        harness.service.upload(DEMO_SUPERUSER, b"chain"),
        harness.service.remove(DEMO_SUPERUSER),
        harness.service.choose(DEMO_SUPERUSER, "acme"),
    ):
        with pytest.raises(DomainError) as refused:
            await call
        assert refused.value.code is ErrorCode.TLS_SOURCE_NOT_APPLICABLE


# --- keeping the volume in step ------------------------------------------------------------


async def test_sync_follows_the_effective_source_and_the_key_on_this_host(
    harness: Harness,
) -> None:
    assert await harness.service.sync() == "withdrawn"

    await harness.request_and_upload()
    harness.source = "own"
    assert await harness.service.sync() == "published"
    assert harness.volume.published == FINGERPRINT

    harness.volume.current = None  # restored elsewhere: ACME applies until the key is back
    assert await harness.service.sync() == "key_missing"
    assert harness.volume.published is None


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
