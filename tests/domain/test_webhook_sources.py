"""The source-network rule of the inbound webhook gate (ADR 0124 points 3 and 4)."""

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.inbound_gate import Refusal
from app.domain.networks import MAX_NETWORKS
from app.domain.public_address import address_is_public
from app.domain.webhook_sources import (
    ChainFault,
    ProxyChain,
    SourceState,
    check_source,
    networks_of,
    networks_that_apply,
    source_state,
    valid_networks,
)

SUPPLIER = "8.8.8.8"
LIST = ("8.8.8.0/24", "2001:4860:4860::/48")
SOUND = ProxyChain(("172.31.240.2",), address_is_public=True, headers_ignored=False)


def chain(*trusted: str, public: bool = True, headers_ignored: bool = False) -> ProxyChain:
    return ProxyChain(trusted, public, headers_ignored)


def test_a_list_is_written_per_line_or_comma_separated_and_kept_canonical() -> None:
    assert networks_of("8.8.8.8, 10.0.0.9/24\n2001:db8::1\n\n8.8.8.8") == (
        "8.8.8.8/32",
        "10.0.0.0/24",
        "2001:db8::1/128",
    )
    assert networks_of("") == ()


@pytest.mark.parametrize("text", ["not-a-network", "203.0.113.0/33", "8.8.8.0/24, nope"])
def test_a_list_with_an_entry_that_is_no_network_is_refused(text: str) -> None:
    with pytest.raises(DomainError) as refused:
        networks_of(text)

    assert (refused.value.code, refused.value.field) == (
        ErrorCode.INVALID_CONFIGURATION,
        "allowed_networks",
    )
    assert not valid_networks(text)


def test_a_list_holds_at_most_32_networks() -> None:
    assert valid_networks(",".join(f"10.0.{n}.0/24" for n in range(MAX_NETWORKS)))
    assert not valid_networks(",".join(f"10.0.{n}.0/24" for n in range(MAX_NETWORKS + 1)))
    assert valid_networks(",".join(["10.0.0.0/24"] * 100))  # the same network is one network


def test_an_own_list_replaces_the_default_and_an_empty_one_falls_back_to_it() -> None:
    own, default = ("198.51.100.0/24",), ("8.8.8.0/24",)

    assert networks_that_apply(own, default) == own
    assert networks_that_apply((), default) == default
    assert networks_that_apply((), ()) == ()


def test_an_address_without_a_list_accepts_any_source_unless_a_list_is_required() -> None:
    assert check_source(SUPPLIER, (), required=False, chain=SOUND) is None
    assert check_source("192.0.2.1", (), required=False, chain=SOUND) is None
    assert check_source(SUPPLIER, (), required=True, chain=SOUND) is Refusal.NETWORK


def test_a_caller_inside_the_list_passes_and_one_outside_it_is_refused_for_its_network() -> None:
    assert check_source(SUPPLIER, LIST, required=False, chain=SOUND) is None
    assert check_source("2001:4860:4860:1::9", LIST, required=True, chain=SOUND) is None
    assert check_source("::ffff:8.8.8.8", LIST, required=False, chain=SOUND) is None
    assert check_source("8.8.4.4", LIST, required=False, chain=SOUND) is Refusal.NETWORK
    assert check_source("2001:4860:4861::1", LIST, required=False, chain=SOUND) is Refusal.NETWORK


@pytest.mark.parametrize("trusted", ["*", "10.0.0.0/8", "172.31.0.0/16", "2001:db8::/32"])
def test_a_proxy_trust_wider_than_single_hosts_makes_the_address_untrustworthy(
    trusted: str,
) -> None:
    """Every address could be spoofed: a list that cannot be evaluated is refused, not passed."""
    wide = chain(trusted)

    assert wide.standing_faults() == (ChainFault.WIDE_TRUST,)
    assert check_source(SUPPLIER, LIST, required=False, chain=wide) is Refusal.SOURCE_UNKNOWN
    # even a caller that is in the list: the address is not believed
    assert check_source("8.8.4.4", LIST, required=True, chain=wide) is Refusal.SOURCE_UNKNOWN


@pytest.mark.parametrize("trusted", ["172.31.240.2", "172.31.240.2/32", "2001:db8::1/128"])
def test_single_hosts_are_a_trustworthy_proxy(trusted: str) -> None:
    assert chain(trusted).standing_faults() == ()
    assert check_source(SUPPLIER, LIST, required=False, chain=chain(trusted)) is None


def test_a_literal_in_the_trusted_peers_names_one_peer_and_is_not_wide() -> None:
    assert chain("/run/proxy.sock").standing_faults() == ()


@pytest.mark.parametrize("caller", ["172.31.240.2", "::ffff:172.31.240.2", "2001:db8:ffff::2"])
def test_a_caller_that_is_one_of_the_trusted_proxies_means_a_hop_did_not_hand_it_on(
    caller: str,
) -> None:
    behind = chain("172.31.240.2", "2001:db8:ffff::2")

    assert behind.fault_for(caller) is ChainFault.CALLER_IS_PROXY
    assert check_source(caller, LIST, required=False, chain=behind) is Refusal.SOURCE_UNKNOWN
    assert behind.fault_for(SUPPLIER) is None


@pytest.mark.parametrize("caller", ["127.0.0.1", "::1", "10.1.2.3", "192.168.0.7", "fd00::7"])
def test_a_loopback_or_private_caller_while_the_public_address_is_public_is_not_believed(
    caller: str,
) -> None:
    assert SOUND.fault_for(caller) is ChainFault.HOP_NOT_HANDED_ON
    assert check_source(caller, ("10.0.0.0/8", "::1/128"), required=False, chain=SOUND) is (
        Refusal.SOURCE_UNKNOWN
    )


def test_a_private_caller_is_fine_when_the_public_address_is_private_too() -> None:
    internal = chain("10.9.9.9", public=False)

    assert internal.fault_for("10.1.2.3") is None
    assert check_source("10.1.2.3", ("10.1.0.0/16",), required=False, chain=internal) is None


def test_forwarded_headers_this_call_carried_and_the_server_ignored_make_it_untrustworthy() -> None:
    """Decided per call: nothing standing, so it is no state a finding or another call can see."""
    ignored = chain("172.31.240.2", headers_ignored=True)

    assert ignored.standing_faults() == ()
    assert ignored.fault_for(SUPPLIER) is ChainFault.UNTRUSTED_HOP
    assert check_source(SUPPLIER, LIST, required=False, chain=ignored) is Refusal.SOURCE_UNKNOWN
    assert check_source(SUPPLIER, LIST, required=False, chain=chain("172.31.240.2")) is None
    assert chain("*", headers_ignored=True).standing_faults() == (ChainFault.WIDE_TRUST,)


@pytest.mark.parametrize("caller", [None, "", "testclient", "not-an-address"])
def test_a_caller_without_an_address_is_not_believed_when_a_list_applies(
    caller: str | None,
) -> None:
    assert SOUND.fault_for(caller) is ChainFault.NO_ADDRESS
    assert check_source(caller, LIST, required=False, chain=SOUND) is Refusal.SOURCE_UNKNOWN


@pytest.mark.parametrize(
    "bad",
    [
        chain("*"),
        chain("172.31.240.2", headers_ignored=True),
        chain("172.31.240.2", public=True),
    ],
)
def test_an_address_without_a_list_is_never_affected_by_the_proxy_chain(bad: ProxyChain) -> None:
    for caller in (SUPPLIER, "172.31.240.2", "127.0.0.1", None):
        assert check_source(caller, (), required=False, chain=bad) is None


def test_the_chain_is_read_from_the_settings_the_way_system_status_shows_them() -> None:
    bundled = ProxyChain.of(
        forwarded_allow_ips=None,
        tls_proxy_bundled=True,
        webhook_base_url="https://planner.voorbeeld.nl",
        headers_ignored=False,
    )
    wide = ProxyChain.of(
        forwarded_allow_ips="*",
        tls_proxy_bundled=False,
        webhook_base_url="http://127.0.0.1:8000",
        headers_ignored=False,
    )

    assert bundled.trusted == ("172.31.240.2",)
    assert bundled.address_is_public
    assert bundled.standing_faults() == ()
    assert wide.standing_faults() == (ChainFault.WIDE_TRUST,)
    assert not wide.address_is_public


def test_the_state_of_an_address_is_never_silent() -> None:
    assert source_state((), required=False) is SourceState.UNRESTRICTED
    assert source_state((), required=True) is SourceState.REQUIRED_MISSING
    assert source_state(LIST, required=False) is SourceState.RESTRICTED
    assert source_state(LIST, required=True) is SourceState.RESTRICTED


@pytest.mark.parametrize(
    ("url", "public"),
    [
        ("https://planner.voorbeeld.nl", True),
        ("https://8.8.8.9:8443", True),
        ("https://[2001:4860::1]", True),
        ("http://127.0.0.1:8000", False),
        ("http://localhost:8000", False),
        ("https://planner.localhost", False),
        ("https://planner.internal", False),
        ("https://planner", False),
        ("https://10.0.0.5", False),
        ("", False),
    ],
)
def test_a_public_address_is_told_by_its_shape(url: str, public: bool) -> None:
    assert address_is_public(url) is public
