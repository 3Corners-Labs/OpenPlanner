"""Pseudonyms of callers for security events (ADR 0053 point 2)."""

import hashlib
import hmac
import re

from app.domain.client_pseudonym import ClientPseudonyms
from app.domain.networks import UNKNOWN_CALLER

KEY = b"k" * 32


def test_the_same_address_gives_the_same_short_pseudonym() -> None:
    pseudonyms = ClientPseudonyms(KEY)

    first = pseudonyms.of("203.0.113.7")

    assert first == pseudonyms.of("203.0.113.7")
    assert re.fullmatch(r"[0-9a-f]{16}", first)


def test_different_addresses_and_keys_give_different_pseudonyms() -> None:
    pseudonyms = ClientPseudonyms(KEY)

    assert pseudonyms.of("203.0.113.7") != pseudonyms.of("203.0.113.8")
    assert ClientPseudonyms(b"x" * 32).of("203.0.113.7") != pseudonyms.of("203.0.113.7")


def test_an_ipv6_host_is_one_caller_per_64_prefix() -> None:
    pseudonyms = ClientPseudonyms(KEY)

    assert pseudonyms.of("2001:db8:1:2::1") == pseudonyms.of("2001:db8:1:2:ffff::9")
    assert pseudonyms.of("2001:db8:1:2::1") != pseudonyms.of("2001:db8:1:3::1")


def test_an_ipv4_mapped_ipv6_address_is_the_ipv4_caller() -> None:
    pseudonyms = ClientPseudonyms(KEY)

    assert pseudonyms.of("::ffff:203.0.113.7") == pseudonyms.of("203.0.113.7")


def test_a_missing_or_malformed_address_is_unknown() -> None:
    pseudonyms = ClientPseudonyms(KEY)

    assert pseudonyms.of(None) == UNKNOWN_CALLER
    assert pseudonyms.of("") == UNKNOWN_CALLER
    assert pseudonyms.of("testclient") == UNKNOWN_CALLER


def _hmac(canonical: str) -> str:
    """What the pseudonym was before ADR 0124 step 2a: HMAC-SHA256 of the canonical text, 16 hex."""
    return hmac.new(KEY, canonical.encode(), hashlib.sha256).hexdigest()[:16]


def test_the_pseudonyms_are_the_same_as_before_the_networks_helper() -> None:
    """A changed pseudonym would break the trail across an upgrade."""
    pseudonyms = ClientPseudonyms(KEY)

    assert pseudonyms.of("203.0.113.7") == _hmac("203.0.113.7")
    assert pseudonyms.of("::ffff:203.0.113.7") == _hmac("203.0.113.7")
    assert pseudonyms.of("2001:db8:1:2::1") == _hmac("2001:db8:1:2::/64")
    assert pseudonyms.of("2001:db8:1:2:ffff::9") == _hmac("2001:db8:1:2::/64")
    assert pseudonyms.of(None) == "unknown"
