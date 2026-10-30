"""Parsing an uploaded chain or key, and making a key with its request (ADR 0070)."""

from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.adapters.certificate_pem import CryptographyPemReader, new_key_and_request
from app.domain.errors import DomainError, ErrorCode
from tests.support.certificates import make_pair
from tests.support.domain import NOW

HOST = "afspraak.example.nl"
SPKI = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def test_a_chain_is_read_with_its_public_data_fingerprint_and_key() -> None:
    pair = make_pair(HOST, not_after=NOW + timedelta(days=30))
    reader = CryptographyPemReader()

    read = reader.read_chain(pair.chain)

    assert read.certificate.names == (HOST,)
    assert read.certificate.subject == f"Gemeente Demo ({HOST})"
    assert read.certificate.not_after == NOW + timedelta(days=30)
    assert len(read.fingerprint) == 64
    assert read.fingerprint == read.fingerprint.lower()
    assert read.chain_pem == pair.chain.decode()
    assert read.public_key == reader.read_key(pair.key).public_key


def test_a_chain_is_kept_in_order_and_stray_text_is_dropped() -> None:
    leaf, intermediate = make_pair(HOST), make_pair("ca.example.nl")
    chain = b"# comment\n" + leaf.chain + b"\r\n" + intermediate.chain

    read = CryptographyPemReader().read_chain(chain)

    assert read.chain_pem == leaf.chain.decode() + intermediate.chain.decode()
    assert read.certificate.names == (HOST,)


def test_a_key_of_another_certificate_has_another_public_key() -> None:
    first, second = make_pair(HOST), make_pair(HOST)
    reader = CryptographyPemReader()

    assert reader.read_chain(first.chain).public_key != reader.read_key(second.key).public_key


def test_a_traditional_ec_key_is_normalised_to_pkcs8() -> None:
    pair = make_pair(HOST)
    private = serialization.load_pem_private_key(pair.key, password=None)
    assert isinstance(private, ec.EllipticCurvePrivateKey)
    traditional = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    read = CryptographyPemReader().read_key(traditional)

    assert read.key_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert read.public_key == CryptographyPemReader().read_key(pair.key).public_key


@pytest.mark.parametrize("content", ["garbage", "empty", "key"])
def test_an_unreadable_chain_is_refused(content: str) -> None:
    pair = make_pair(HOST)  # built here: random keys would give every xdist worker other ids
    inputs = {"garbage": b"not pem", "empty": b"", "key": pair.key}

    with pytest.raises(DomainError) as refused:
        CryptographyPemReader().read_chain(inputs[content])

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_UNREADABLE
    assert refused.value.field == "chain"


@pytest.mark.parametrize("content", ["garbage", "chain", "encrypted"])
def test_an_unreadable_or_encrypted_key_is_refused(content: str) -> None:
    pair = make_pair(HOST)
    private = serialization.load_pem_private_key(pair.key, password=None)
    encrypted = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"pass"),
    )
    inputs = {"garbage": b"not a key", "chain": pair.chain, "encrypted": encrypted}

    with pytest.raises(DomainError) as refused:
        CryptographyPemReader().read_key(inputs[content])

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_UNREADABLE
    assert refused.value.field == "key"


def test_a_new_key_comes_with_a_request_for_the_host() -> None:
    key, request = new_key_and_request(HOST)

    private = serialization.load_pem_private_key(key.encode(), password=None)
    csr = x509.load_pem_x509_csr(request.encode())
    assert isinstance(private, ec.EllipticCurvePrivateKey)
    assert private.curve.name == "secp256r1"
    assert csr.is_signature_valid
    assert csr.subject.rfc4514_string() == f"CN={HOST}"
    names = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == [HOST]
    read = CryptographyPemReader().read_key(key.encode())
    assert read.public_key == csr.public_key().public_bytes(*SPKI)
