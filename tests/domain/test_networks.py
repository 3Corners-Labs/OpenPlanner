"""Addresses and networks: the one home for parsing, matching and counting callers (ADR 0124)."""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.networks import (
    UNKNOWN_CALLER,
    address_of,
    caller_key,
    each_within,
    in_any,
    parse_networks,
    pinned_network,
    split_entries,
)


def test_a_list_is_canonical_deduplicated_and_in_order() -> None:
    entries = ["  10.0.0.7/24", "", "10.0.0.0/24", "2001:db8::1", "192.0.2.1"]

    assert parse_networks(entries, maximum=5) == ("10.0.0.0/24", "2001:db8::1/128", "192.0.2.1/32")


def test_a_text_is_split_on_lines_and_commas() -> None:
    assert parse_networks(split_entries("10.20.0.0/16\n2001:db8:ab12::/48"), maximum=5) == (
        "10.20.0.0/16",
        "2001:db8:ab12::/48",
    )
    assert parse_networks(split_entries("10.20.4.9, 10.20.4.9 ,192.0.2.1"), maximum=5) == (
        "10.20.4.9/32",
        "192.0.2.1/32",
    )
    assert parse_networks(split_entries("  \n,"), maximum=5) == ()
    assert parse_networks([], maximum=5) == ()


@pytest.mark.parametrize("entry", ["not-a-network", "10.0.0.0/33", "2001:db8::/129", "1.2.3", "*"])
def test_garbage_is_refused_with_the_field(entry: str) -> None:
    for entries in ([entry], entry):
        with pytest.raises(DomainError) as caught:
            parse_networks(entries, maximum=5, field="allowed")
        assert (caught.value.code, caught.value.field) == (
            ErrorCode.INVALID_CONFIGURATION,
            "allowed",
        )


def test_the_maximum_counts_different_networks() -> None:
    at_most = [f"10.0.{i}.0/24" for i in range(3)]

    assert len(parse_networks(at_most, maximum=3)) == 3
    assert len(parse_networks(at_most + at_most, maximum=3)) == 3  # a repeat is not another one
    with pytest.raises(DomainError) as caught:
        parse_networks([*at_most, "10.0.9.0/24"], maximum=3)
    assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, "networks")


def test_a_text_is_split_on_commas_and_line_ends() -> None:
    assert split_entries("a, b\nc\r\nd") == ["a", " b", "c", "d"]
    assert split_entries("") == []


def test_an_ipv4_address_written_as_ipv6_is_the_ipv4_address() -> None:
    assert str(address_of("::ffff:203.0.113.7")) == "203.0.113.7"
    assert str(address_of("203.0.113.7")) == "203.0.113.7"
    assert str(address_of("2001:db8::7")) == "2001:db8::7"


@pytest.mark.parametrize("text", [None, "", "testclient", "203.0.113", "10.0.0.0/8"])
def test_no_address_is_none(text: str | None) -> None:
    assert address_of(text) is None


def test_an_address_is_matched_against_the_networks() -> None:
    networks = ("10.20.0.0/16", "2001:db8::/32")

    assert in_any("10.20.4.31", networks)
    assert in_any("2001:db8:1::5", networks)
    assert not in_any("10.21.4.31", networks)
    assert not in_any("2001:db9::1", networks)


def test_an_ipv4_mapped_ipv6_address_matches_an_ipv4_network() -> None:
    assert in_any("::ffff:10.20.4.31", ("10.20.0.0/16",))
    assert not in_any("::ffff:10.21.4.31", ("10.20.0.0/16",))


@pytest.mark.parametrize("listed", ["::ffff:10.20.0.0/112", "::ffff:0:0/96", "::/0"])
def test_a_mapped_address_is_also_read_as_written_against_ipv6_networks(listed: str) -> None:
    assert in_any("::ffff:10.20.4.31", (listed,))
    assert not in_any("::ffff:10.20.4.31", ("2001:db8::/32",))


def test_an_address_of_the_other_family_matches_nothing_and_is_no_error() -> None:
    assert not in_any("2001:db8::1", ("10.0.0.0/8",))
    assert not in_any("10.0.0.1", ("2001:db8::/32",))


def test_garbage_and_a_missing_address_match_nothing_and_an_empty_list_holds_no_address() -> None:
    """The helper gives an empty list no meaning; every caller keeps its own rule."""
    assert not in_any("garbage", ("10.0.0.0/8",))
    assert not in_any(None, ("10.0.0.0/8",))
    assert not in_any("10.0.0.1", ())  # what an empty list means is the caller's rule


def test_inside_is_a_subnet_of_one_of_the_outer_networks() -> None:
    outer = ("10.20.0.0/16", "2001:db8::/32")

    assert each_within(("10.20.4.0/24", "2001:db8:1::/48"), outer)
    assert each_within(("10.20.0.0/16",), outer)
    assert not each_within(("10.0.0.0/8",), outer)  # wider than the outer one
    assert not each_within(("10.20.4.0/24", "10.30.0.0/16"), outer)  # one outside is enough
    assert not each_within(("2001:db8::/32",), ("10.0.0.0/8",))  # another family is never inside
    assert each_within((), outer)
    assert not each_within(("10.20.0.0/16",), ())


def test_a_pinned_network_is_a_32_or_a_64() -> None:
    assert pinned_network("10.20.4.31") == "10.20.4.31/32"
    assert pinned_network("2001:db8:ab12:5:1:2:3:4") == "2001:db8:ab12:5::/64"
    assert pinned_network("::ffff:10.20.4.31") == "10.20.4.31/32"  # not the whole ::/64
    assert pinned_network("not an address") is None
    assert pinned_network(None) is None


def test_ipv4_is_counted_as_it_is_and_ipv6_by_its_64() -> None:
    assert caller_key("203.0.113.7") == "203.0.113.7"
    assert caller_key("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    # Two addresses of one /64 are one caller, two /64s are two.
    assert caller_key("2001:db8:1:2::1") == caller_key("2001:db8:1:2:ffff:eeee:dddd:cccc")
    assert caller_key("2001:db8:1:2::1") != caller_key("2001:db8:1:3::1")


def test_an_ipv4_mapped_caller_has_the_key_of_its_ipv4_form() -> None:
    assert caller_key("::ffff:203.0.113.7") == caller_key("203.0.113.7") == "203.0.113.7"


def test_a_caller_without_a_readable_address_still_has_a_key() -> None:
    assert caller_key(None) == UNKNOWN_CALLER
    assert caller_key("") == UNKNOWN_CALLER
    assert caller_key("testclient") == "testclient"  # no address: its own key, no /64 rule
