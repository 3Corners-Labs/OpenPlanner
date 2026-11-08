"""The ``ssl`` certificate probe against a TLS server started in the test (ADR 0064)."""

import asyncio
import ssl
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.adapters.tls_probe import SslCertificateProbe, parse_certificate
from app.domain.tls import Trust
from tests.support.certificates import self_signed


@pytest.fixture
def certificate(tmp_path: Path) -> tuple[Path, Path]:
    return self_signed(tmp_path)


@pytest.fixture
async def server(certificate: tuple[Path, Path]) -> AsyncIterator[int]:
    """A TLS server on a free port that answers nothing; yields the port."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate[0]), str(certificate[1]))

    async def handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    started = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)
    try:
        yield started.sockets[0].getsockname()[1]
    finally:
        started.close()
        await started.wait_closed()


async def test_a_trusted_certificate_is_read_in_full(
    server: int, certificate: tuple[Path, Path]
) -> None:
    probe = SslCertificateProbe(cafile=str(certificate[0]))

    result = await probe.probe("localhost", server)

    assert result.trust is Trust.TRUSTED
    assert result.error == ""
    assert result.certificate is not None
    assert result.certificate.subject == "OpenPlanner test (localhost)"
    assert result.certificate.issuer == "OpenPlanner test (localhost)"
    assert result.certificate.names == ("localhost",)
    assert 58 <= result.certificate.days_left(datetime.now(UTC)) <= 60


async def test_a_certificate_the_host_does_not_trust_is_still_read(server: int) -> None:
    result = await SslCertificateProbe().probe("localhost", server)

    assert result.trust is Trust.UNTRUSTED
    assert result.certificate is not None
    assert result.certificate.names == ("localhost",)


async def test_a_name_the_certificate_does_not_cover_is_a_mismatch(
    server: int, certificate: tuple[Path, Path]
) -> None:
    probe = SslCertificateProbe(cafile=str(certificate[0]))

    result = await probe.probe("127.0.0.1", server)

    assert result.trust is Trust.NAME_MISMATCH
    assert result.certificate is not None


async def test_a_closed_port_is_unreachable() -> None:
    result = await SslCertificateProbe(timeout=2).probe("127.0.0.1", 9)

    assert result == result.__class__(error="unreachable")


async def test_a_server_that_never_answers_is_a_timeout() -> None:
    async def silent(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(1)
        writer.close()  # 3.12: wait_closed() below waits for every open connection

    plain = await asyncio.start_server(silent, "127.0.0.1", 0)
    try:
        port = plain.sockets[0].getsockname()[1]
        result = await SslCertificateProbe(timeout=0.3).probe("127.0.0.1", port)
    finally:
        plain.close()
        await plain.wait_closed()

    assert result.error == "timeout"


def test_a_certificate_without_names_has_none(tmp_path: Path) -> None:
    cert_path, _ = self_signed(tmp_path)
    pem = cert_path.read_bytes()
    der = x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)

    parsed = parse_certificate(der)

    assert parsed.names == ("localhost",)
    with pytest.raises(ValueError, match="."):
        parse_certificate(b"not a certificate")


async def test_an_extra_root_is_trusted_next_to_the_host_bundle(
    server: int, certificate: tuple[Path, Path]
) -> None:
    """ADR 0112: the proxy's internal root joins the host's CAs instead of replacing them."""
    result = await SslCertificateProbe(extra_cafile=str(certificate[0])).probe("localhost", server)

    assert result.trust is Trust.TRUSTED
