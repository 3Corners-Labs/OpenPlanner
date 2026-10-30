"""The directory shared with the proxy: keys, request and the served pair (ADR 0070)."""

import stat
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.adapters.certificate_pem import CryptographyPemReader
from app.adapters.certificate_volume import FileCertificateVolume
from app.domain.errors import DomainError, ErrorCode
from tests.support.certificates import make_pair

HOST = "afspraak.example.nl"
FINGERPRINT = "ab" * 32
CHAIN = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
SPKI = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "portal-certs"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_an_empty_volume_has_no_keys(directory: Path) -> None:
    assert FileCertificateVolume(directory).keys().current is None
    assert FileCertificateVolume(directory).keys().pending is None
    assert FileCertificateVolume(directory).keys().pending_csr is None


def test_a_request_makes_a_private_pending_key_and_a_readable_csr(directory: Path) -> None:
    volume = FileCertificateVolume(directory)

    request = volume.generate_pending(HOST)

    assert (directory / "pending.csr").read_text() == request
    assert mode(directory / "pending.key") == 0o600
    assert mode(directory / "pending.csr") == 0o644
    keys = volume.keys()
    assert keys.current is None
    assert keys.pending_csr == request
    csr = x509.load_pem_x509_csr(request.encode())
    assert keys.pending == csr.public_key().public_bytes(*SPKI)
    assert not list(directory.glob("*.tmp"))


def test_a_new_request_replaces_the_pending_one_and_leaves_the_served_key(
    directory: Path,
) -> None:
    volume = FileCertificateVolume(directory)
    volume.import_key(make_pair(HOST).key.decode())
    served = volume.keys().current
    first = volume.generate_pending(HOST)

    second = volume.generate_pending(HOST)

    assert second != first
    assert volume.keys().pending_csr == second
    assert volume.keys().current == served


def test_promoting_makes_the_pending_key_the_served_one(directory: Path) -> None:
    volume = FileCertificateVolume(directory)
    volume.generate_pending(HOST)
    pending = volume.keys().pending

    volume.promote_pending()

    keys = volume.keys()
    assert keys.current == pending
    assert keys.pending is None
    assert keys.pending_csr is None
    assert mode(directory / "privkey.pem") == 0o600


def test_importing_writes_the_served_key_and_drops_a_pending_request(directory: Path) -> None:
    volume = FileCertificateVolume(directory)
    volume.generate_pending(HOST)
    pair = make_pair(HOST)

    volume.import_key(pair.key.decode())

    assert (directory / "privkey.pem").read_bytes() == pair.key
    assert mode(directory / "privkey.pem") == 0o600
    assert volume.keys().current == CryptographyPemReader().read_key(pair.key).public_key
    assert volume.keys().pending is None
    assert not (directory / "pending.csr").exists()


def test_publishing_writes_the_chain_then_the_serial_and_withdrawing_keeps_the_key(
    directory: Path,
) -> None:
    volume = FileCertificateVolume(directory)
    volume.import_key(make_pair(HOST).key.decode())

    volume.publish(CHAIN, FINGERPRINT)

    assert (directory / "fullchain.pem").read_text() == CHAIN
    assert (directory / "serial").read_text() == FINGERPRINT + "\n"
    assert not list(directory.glob("*.tmp"))

    (directory / "fullchain.pem").write_text("left alone")
    volume.publish(CHAIN, FINGERPRINT)  # the same serial is not written twice
    assert (directory / "fullchain.pem").read_text() == "left alone"

    volume.publish("new chain", "cd" * 32)
    assert (directory / "serial").read_text().strip() == "cd" * 32

    volume.withdraw()
    volume.withdraw()  # nothing there is fine
    assert sorted(p.name for p in directory.iterdir()) == ["privkey.pem"]


def test_publishing_without_a_key_on_this_host_writes_nothing(directory: Path) -> None:
    FileCertificateVolume(directory).publish(CHAIN, FINGERPRINT)

    assert not directory.exists() or not (directory / "serial").exists()


def test_an_unreadable_key_file_counts_as_no_key(directory: Path) -> None:
    directory.mkdir()
    (directory / "privkey.pem").write_text("not a key")

    assert FileCertificateVolume(directory).keys().current is None


def test_a_volume_that_cannot_be_written_refuses_a_request_and_never_raises_elsewhere(
    tmp_path: Path,
) -> None:
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("a file where the directory should be")
    volume = FileCertificateVolume(blocked / "portal-certs")

    with pytest.raises(DomainError) as refused:
        volume.generate_pending(HOST)
    with pytest.raises(DomainError):
        volume.import_key(make_pair(HOST).key.decode())
    volume.publish(CHAIN, FINGERPRINT)
    volume.withdraw()
    assert volume.keys().current is None

    assert refused.value.code is ErrorCode.TLS_VOLUME_UNWRITABLE
    assert blocked.read_text() == "a file where the directory should be"
