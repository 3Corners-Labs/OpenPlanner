"""The list of allowed source networks: what may be written, what may be read back (ADR 0124)."""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.networks import NetworkList
from app.domain.source_networks import (
    MAX_WEBHOOK_NETWORKS,
    read_stored_networks,
    source_networks_of,
    valid_source_networks,
)

UNREADABLE = NetworkList(unreadable=True)


def refused_field(text: str) -> str:
    with pytest.raises(DomainError) as refused:
        source_networks_of(text)
    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION
    return refused.value.field


def test_a_list_is_written_per_line_or_comma_separated_and_kept_canonical() -> None:
    assert source_networks_of("8.8.8.8, 10.0.0.9/24\n2001:db8::1\n\n8.8.8.8") == (
        "8.8.8.8/32",
        "10.0.0.0/24",
        "2001:db8::1/128",
    )
    assert source_networks_of("") == ()


@pytest.mark.parametrize(
    "text",
    [
        "not-a-network",
        "203.0.113.0/33",
        "8.8.8.0/24, nope",
        "0.0.0.0/0",
        "::/0",
        "8.8.8.0/24\n0.0.0.0/0",
        "0.0.0.1/0",  # host bits are cleared: this is 0.0.0.0/0
        # an IPv6 network that holds all of ::ffff:0:0/96 lets every IPv4 caller in
        "::/1",
        "::/8",
        "::/16",
        "::ffff:0:0/96",
        "::ffff:0:0/80",
        "8.8.8.0/24, ::/2",
    ],
)
def test_a_list_that_is_no_list_or_lets_a_whole_address_family_in_is_refused(text: str) -> None:
    """A network covering a whole family is "any source" called "restricted"."""
    assert refused_field(text) == "allowed_networks"
    assert not valid_source_networks(text)


def test_a_wide_network_is_a_choice_of_the_organisation_and_only_the_whole_family_is_refused() -> (
    None
):
    # two halves of a family are not refused, on purpose: how wide a list is, is its owner's choice
    assert valid_source_networks("0.0.0.0/1, 128.0.0.0/1, 8.0.0.0/8")
    assert valid_source_networks("8000::/1")  # the half of IPv6 that holds no IPv4-mapped address
    assert valid_source_networks("::ffff:0:0/97, ::ffff:8.0.0.0/104")  # a part of the mapped range
    assert valid_source_networks("::/97")


def test_a_list_holds_at_most_32_networks() -> None:
    nets = [f"10.0.{n}.0/24" for n in range(MAX_WEBHOOK_NETWORKS + 1)]

    assert valid_source_networks(",".join(nets[:MAX_WEBHOOK_NETWORKS]))
    assert not valid_source_networks(",".join(nets))
    assert valid_source_networks(",".join(["10.0.0.0/24"] * 100))  # the same network is one


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ([], NetworkList()),
        (["8.8.8.0/24", "8.8.8.8/32"], NetworkList(("8.8.8.0/24", "8.8.8.8/32"))),
        (["2001:4860:4860::/48"], NetworkList(("2001:4860:4860::/48",))),
        # the writing rule accepts these, but nothing canonical was stored: unreadable
        (["8.8.8.8"], UNREADABLE),
        (["8.8.8.0/24", "8.8.8.0/24"], UNREADABLE),  # a duplicate
        (["8.8.8.0/24, 8.8.9.0/24"], UNREADABLE),  # one entry that is two networks
        (["8.8.8.0/24\n8.8.9.0/24"], UNREADABLE),
        (["2001:4860:4860:0:0:0:0:0/48"], UNREADABLE),
        (["not-a-network"], UNREADABLE),
        ([""], UNREADABLE),
        (["8.8.8.9/24"], UNREADABLE),  # host bits: nothing wrote this
        (["8.8.8.0/24", 7], UNREADABLE),
        (["0.0.0.0/0"], UNREADABLE),
        (["::/0"], UNREADABLE),
        (["::/1"], UNREADABLE),  # the same family rule as writing
        (["::ffff:0:0/96"], UNREADABLE),
        ("8.8.8.0/24", UNREADABLE),  # a string is not a list
        ({"a": 1}, UNREADABLE),
        (None, UNREADABLE),
        (7, UNREADABLE),
        ([f"10.0.{n}.0/24" for n in range(MAX_WEBHOOK_NETWORKS + 1)], UNREADABLE),
    ],
)
def test_a_stored_list_is_read_back_or_is_unreadable_never_empty(
    stored: object, expected: NetworkList
) -> None:
    assert read_stored_networks(stored) == expected


def test_a_network_list_cannot_hold_networks_and_be_unreadable() -> None:
    with pytest.raises(ValueError, match="cannot be read"):
        NetworkList(("8.8.8.0/24",), unreadable=True)


@pytest.mark.parametrize(
    ("sources", "configured"),
    [(NetworkList(), False), (NetworkList(("8.8.8.0/24",)), True), (UNREADABLE, True)],
)
def test_a_list_is_configured_when_it_holds_networks_or_cannot_be_read(
    sources: NetworkList, configured: bool
) -> None:
    assert sources.configured is configured
