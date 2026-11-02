"""The public address: shape, links, the nonce and the judgement of a round trip (ADR 0086)."""

import pytest

from app.domain.public_address import (
    AddressFinding,
    Forwarded,
    InstanceAnswer,
    InstanceReply,
    PeerClass,
    Shape,
    classify_peer,
    forwarded_state,
    judge,
    nonce_hash,
    nonce_matches,
    origin_of,
    shape_of,
    trusted_proxy,
    url_on,
)

ID = "11111111-1111-4111-8111-111111111111"


def answer(*, instance: str = ID, scheme: str | None = "https", fwd: Forwarded | None = None):
    return InstanceReply(InstanceAnswer(instance, scheme, fwd or Forwarded.HONOURED))


def test_the_shape_follows_the_bundled_proxy_and_the_trusted_address() -> None:
    assert shape_of(tls_proxy_bundled=True, forwarded_allow_ips=None) is Shape.BUNDLED
    assert shape_of(tls_proxy_bundled=False, forwarded_allow_ips="10.0.0.5") is Shape.OWN
    assert shape_of(tls_proxy_bundled=False, forwarded_allow_ips="  ") is Shape.NONE
    assert shape_of(tls_proxy_bundled=False, forwarded_allow_ips=None) is Shape.NONE


def test_the_trusted_proxy_is_the_named_one_or_the_bundled_one() -> None:
    assert trusted_proxy("10.0.0.5, 10.0.0.6", tls_proxy_bundled=False) == ("10.0.0.5", "10.0.0.6")
    assert trusted_proxy(None, tls_proxy_bundled=True) == ("172.31.240.2",)
    assert trusted_proxy(None, tls_proxy_bundled=False) == ()


def test_a_link_is_built_on_the_public_address_only() -> None:
    assert (
        url_on("https://op.example.nl/", "/admin/callback")
        == "https://op.example.nl/admin/callback"
    )
    assert origin_of("https://op.example.nl:8443/x?y=1") == "https://op.example.nl:8443"
    assert origin_of("https://user:pw@op.example.nl/") == "https://op.example.nl"
    assert origin_of("http://[::1]:8000/") == "http://[::1]:8000"


def test_the_nonce_is_kept_as_a_hash_and_compared_in_constant_time() -> None:
    digest = nonce_hash("abc")

    assert digest != "abc"
    assert nonce_matches(digest, "abc")
    assert not nonce_matches(digest, "abd")
    assert not nonce_matches(None, "abc")
    assert not nonce_matches(digest, "")


@pytest.mark.parametrize(
    ("reply", "https", "expected"),
    [
        (answer(), True, AddressFinding.OK),
        (InstanceReply(redirected=True), True, AddressFinding.REDIRECTED),
        (InstanceReply(error="timeout"), True, AddressFinding.UNREACHABLE),
        (answer(instance="other"), True, AddressFinding.OTHER_INSTANCE),
        (InstanceReply(InstanceAnswer(ID)), True, AddressFinding.OTHER_INSTANCE),
        (answer(fwd=Forwarded.IGNORED, scheme="http"), True, AddressFinding.PROXY_NOT_TRUSTED),
        (answer(fwd=Forwarded.ABSENT, scheme="http"), True, AddressFinding.NO_PROXY_HEADERS),
        (answer(scheme="http"), True, AddressFinding.NOT_HTTPS),
        (answer(scheme="http", fwd=Forwarded.ABSENT), False, AddressFinding.NOT_HTTPS),
    ],
)
def test_a_round_trip_is_judged_in_one_word(
    reply: InstanceReply, https: bool, expected: AddressFinding
) -> None:
    assert judge(reply, instance_id=ID, public_https=https) is expected


def test_the_tunnel_trial_is_found_as_a_proxy_that_is_not_trusted() -> None:
    # 1 November 2026: the proxy sends X-Forwarded-Proto, the app keeps http because the
    # compose gateway is not in FORWARDED_ALLOW_IPS.
    seen = forwarded_state(
        forwarded_for="203.0.113.9",
        forwarded_proto="https",
        client="172.18.0.1",
        scheme="http",
    )

    assert seen is Forwarded.IGNORED
    assert (
        judge(InstanceReply(InstanceAnswer(ID, "http", seen)), instance_id=ID, public_https=True)
        is AddressFinding.PROXY_NOT_TRUSTED
    )


def test_forwarded_headers_are_honoured_when_the_app_took_the_visitors_address() -> None:
    assert (
        forwarded_state(
            forwarded_for="203.0.113.9, 172.18.0.1",
            forwarded_proto="https",
            client="203.0.113.9",
            scheme="https",
        )
        is Forwarded.HONOURED
    )
    assert (
        forwarded_state(forwarded_for=None, forwarded_proto="https", client="x", scheme="https")
        is Forwarded.HONOURED
    )
    assert (
        forwarded_state(forwarded_for=None, forwarded_proto="https", client="x", scheme="http")
        is Forwarded.IGNORED
    )
    assert (
        forwarded_state(forwarded_for=None, forwarded_proto=None, client="x", scheme="http")
        is Forwarded.ABSENT
    )


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1", PeerClass.LOOPBACK),
        ("::1", PeerClass.LOOPBACK),
        ("172.18.0.1", PeerClass.PRIVATE),
        ("10.1.2.3", PeerClass.PRIVATE),
        ("8.8.8.8", PeerClass.PUBLIC),
        ("not an address", PeerClass.PUBLIC),
        (None, PeerClass.PUBLIC),
    ],
)
def test_a_peer_is_only_ever_known_by_its_class(address: str | None, expected: PeerClass) -> None:
    assert classify_peer(address) is expected
