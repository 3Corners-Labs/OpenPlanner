"""Parsing an uploaded chain and key with ``cryptography`` (ADR 0066)."""

from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.adapters.certificate_pem import PemPairReader
from app.domain.errors import DomainError, ErrorCode
from tests.support.certificates import make_pair
from tests.support.domain import NOW

HOST = "afspraak.example.nl"


def test_a_matching_pair_is_read_with_its_public_data_and_fingerprint() -> None:
    pair = make_pair(HOST, not_after=NOW + timedelta(days=30))

    read = PemPairReader().read(pair.chain, pair.key)

    assert read.key_matches
    assert read.certificate.names == (HOST,)
    assert read.certificate.subject == f"Gemeente Demo ({HOST})"
    assert read.certificate.not_after == NOW + timedelta(days=30)
    assert len(read.fingerprint) == 64
    assert read.fingerprint == read.fingerprint.lower()
    assert read.chain_pem == pair.chain.decode()
    assert read.key_pem == pair.key.decode()


def test_a_chain_is_kept_in_order_and_stray_text_is_dropped() -> None:
    leaf, intermediate = make_pair(HOST), make_pair("ca.example.nl")
    chain = b"# comment\n" + leaf.chain + b"\r\n" + intermediate.chain

    read = PemPairReader().read(chain, leaf.key)

    assert read.chain_pem == leaf.chain.decode() + intermediate.chain.decode()
    assert read.certificate.names == (HOST,)


def test_a_key_of_another_certificate_does_not_match() -> None:
    first, second = make_pair(HOST), make_pair(HOST)

    assert PemPairReader().read(first.chain, second.key).key_matches is False


def test_a_traditional_ec_key_is_normalised_to_pkcs8() -> None:
    pair = make_pair(HOST)
    private = serialization.load_pem_private_key(pair.key, password=None)
    assert isinstance(private, ec.EllipticCurvePrivateKey)
    traditional = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    read = PemPairReader().read(pair.chain, traditional)

    assert read.key_matches
    assert read.key_pem.startswith("-----BEGIN PRIVATE KEY-----")


@pytest.mark.parametrize(
    ("chain", "key", "field"),
    [
        ("garbage", "key", "chain"),
        ("empty", "key", "chain"),
        ("chain", "garbage", "key"),
        ("chain", "chain", "key"),  # a certificate as the key
    ],
)
def test_unreadable_input_is_refused_by_file(chain: str, key: str, field: str) -> None:
    pair = make_pair(HOST)  # built here: random keys would give every xdist worker other ids
    inputs = {"chain": pair.chain, "key": pair.key, "garbage": b"not pem", "empty": b""}

    with pytest.raises(DomainError) as refused:
        PemPairReader().read(inputs[chain], inputs[key])

    assert refused.value.code is ErrorCode.TLS_CERTIFICATE_UNREADABLE
    assert refused.value.field == field


def test_an_encrypted_key_is_refused() -> None:
    pair = make_pair(HOST)
    private = serialization.load_pem_private_key(pair.key, password=None)
    encrypted = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"pass"),
    )

    with pytest.raises(DomainError) as refused:
        PemPairReader().read(pair.chain, encrypted)

    assert refused.value.field == "key"
