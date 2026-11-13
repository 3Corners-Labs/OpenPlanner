"""The display link's rules: tokens, states and the address checks (ADR 0117 step 14)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.errors import DomainError
from app.domain.networks import pinned_network
from app.domain.wallboard_link import (
    LinkStatus,
    Refusal,
    WallboardLink,
    allowed_from,
    hash_token,
    inside,
    new_token,
    parse_networks,
    refusal_for,
    same_hash,
    status_of,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _link(**changes: object) -> WallboardLink:
    base = {
        "id": 1,
        "organisation_id": 1,
        "organisation_slug": "acme",
        "group_id": None,
        "group_slug": None,
        "name": "TV",
        "token_hash": hash_token("t"),
        "created_by": "sub",
        "created_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=5),
    }
    return WallboardLink(**{**base, **changes})  # type: ignore[arg-type]


def test_a_token_is_32_random_bytes_and_only_its_hash_is_compared() -> None:
    token = new_token()

    assert len(token) >= 43
    assert token != new_token()
    assert same_hash(hash_token(token), token)
    assert not same_hash(hash_token(token), token + "x")
    assert not same_hash(None, token)


def test_the_states_follow_use_expiry_and_revocation() -> None:
    assert status_of(_link(), NOW) is LinkStatus.NOT_OPENED
    assert status_of(_link(used_at=NOW), NOW) is LinkStatus.ACTIVE
    assert status_of(_link(used_at=NOW), NOW + timedelta(days=6)) is LinkStatus.EXPIRED
    revoked = _link(used_at=NOW, revoked_at=NOW, expires_at=NOW - timedelta(days=1))
    assert status_of(revoked, NOW) is LinkStatus.REVOKED  # a revocation outranks an expiry


def test_the_scope_is_the_group_or_the_organisations_groups() -> None:
    assert _link().scope().organisations == {"acme"}
    assert _link(group_slug="balie").scope().groups == {("acme", "balie")}


def test_networks_are_ranges_in_canonical_form() -> None:
    assert parse_networks("10.20.0.0/16\n2001:db8:ab12::/48") == (
        "10.20.0.0/16",
        "2001:db8:ab12::/48",
    )
    assert parse_networks("10.20.4.9, 10.20.4.9") == ("10.20.4.9/32",)  # a host, once
    assert parse_networks("10.20.4.9/16") == ("10.20.0.0/16",)  # bits right of the prefix go
    assert parse_networks("  \n") == ()


@pytest.mark.parametrize("text", ["nonsense", "10.0.0.0/33", "10.0.0.0/8 extra"])
def test_a_network_that_is_no_range_is_refused(text: str) -> None:
    with pytest.raises(DomainError) as refused:
        parse_networks(text)

    assert refused.value.field == "networks"


def test_more_than_twenty_ranges_are_refused() -> None:
    text = "\n".join(f"10.0.{i}.0/24" for i in range(21))

    with pytest.raises(DomainError):
        parse_networks(text)
    assert len(parse_networks("\n".join(f"10.0.{i}.0/24" for i in range(20)))) == 20


def test_a_link_may_only_narrow_the_default() -> None:
    default = ("10.20.0.0/16",)

    assert inside(("10.20.4.0/24",), default)
    assert not inside(("10.30.0.0/16",), default)
    assert not inside((), default)  # any network is wider than a default
    assert inside((), ())
    assert inside(("10.30.0.0/16",), ())  # no default: anything goes
    assert not inside(("2001:db8::/32",), default)  # another family is never inside


def test_a_pin_is_a_32_for_ipv4_and_a_64_for_ipv6() -> None:
    assert pinned_network("10.20.4.31") == "10.20.4.31/32"
    assert pinned_network("2001:db8:ab12:5:1:2:3:4") == "2001:db8:ab12:5::/64"
    assert pinned_network("not an address") is None
    assert pinned_network("") is None


def test_an_address_is_allowed_by_an_empty_list_or_a_range() -> None:
    assert allowed_from("203.0.113.9", ())
    assert allowed_from("10.20.4.31", ("10.20.0.0/16",))
    assert not allowed_from("10.21.4.31", ("10.20.0.0/16",))
    assert not allowed_from("garbage", ("10.20.0.0/16",))
    assert not allowed_from("2001:db8::1", ("10.20.0.0/16",))


@pytest.mark.parametrize(
    "listed", ["10.20.0.0/16", "::/0", "::ffff:0:0/96", "::ffff:10.20.0.0/112"]
)
def test_a_caller_written_as_ipv6_matches_what_lets_it_in_in_either_reading(listed: str) -> None:
    """The display links share the helper (ADR 0124 step 2a): both readings of a mapped caller."""
    assert allowed_from("::ffff:10.20.4.31", (listed,))
    assert not allowed_from("::ffff:10.20.4.31", ("2001:db8::/32",))
    assert pinned_network("::ffff:10.20.4.31") == "10.20.4.31/32"  # its pin is the IPv4 /32


def test_a_plain_ipv6_caller_is_not_allowed_by_an_ipv4_network() -> None:
    assert not allowed_from("2001:db8::1", ("10.20.0.0/16",))


def test_ipv6_networks_are_not_inside_a_list_of_ipv4_networks() -> None:
    assert not inside(("2001:db8::/32",), ("10.0.0.0/8",))
    assert not inside(("::/0",), ("10.0.0.0/8",))


def test_the_acl_and_the_pin_both_apply_and_the_acl_is_asked_first() -> None:
    link = _link(networks=("10.20.0.0/16",), pinned_network="10.20.4.31/32")

    assert refusal_for(link, "10.20.4.31") is None
    assert refusal_for(link, "10.20.4.32") is Refusal.PIN  # inside the ACL, not the pin
    assert refusal_for(link, "192.0.2.1") is Refusal.NETWORK
    assert refusal_for(_link(), "192.0.2.1") is None  # no ACL, no pin: any address


def test_an_ipv4_mapped_ipv6_address_is_the_ipv4_address() -> None:
    assert pinned_network("::ffff:10.20.4.31") == "10.20.4.31/32"  # not the whole ::/64
    assert allowed_from("::ffff:10.20.4.31", ("10.20.0.0/16",))
    assert not allowed_from("::ffff:10.21.4.31", ("10.20.0.0/16",))
