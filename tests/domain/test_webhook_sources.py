"""The gate's rule for where a webhook may come from (ADR 0124 points 3 and 4).

What a list may hold, and how one is read back, is ``tests/domain/test_source_networks.py``.
"""

import pytest

from app.domain.inbound_gate import Refusal
from app.domain.networks import NetworkList
from app.domain.webhook_sources import (
    ChainFault,
    ProxyChain,
    SourceState,
    check_source,
    judge_source,
    source_list_that_applies,
    source_state,
)

SUPPLIER = "8.8.8.8"
LISTED = NetworkList(("8.8.8.0/24", "2001:4860:4860::/48"))
NONE = NetworkList()
UNREADABLE = NetworkList(unreadable=True)
SOUND = ProxyChain(("172.31.240.2",), address_is_public=True, headers_ignored=False)


def chain(*trusted: str, public: bool = True, headers_ignored: bool = False) -> ProxyChain:
    return ProxyChain(trusted, public, headers_ignored)


def test_an_own_list_replaces_the_default_and_an_empty_one_falls_back_to_it() -> None:
    own, default = NetworkList(("198.51.100.0/24",)), LISTED

    assert source_list_that_applies(own, default) == own
    assert source_list_that_applies(NONE, default) == default
    assert source_list_that_applies(NONE, NONE) == NONE
    assert source_list_that_applies(NONE, UNREADABLE) == UNREADABLE


def test_an_unreadable_own_list_is_not_replaced_by_the_default() -> None:
    assert source_list_that_applies(UNREADABLE, LISTED) == UNREADABLE


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("caller", [SUPPLIER, "8.8.4.4", "172.31.240.2", None])
def test_an_unreadable_list_refuses_every_call_for_its_network_never_passes_it(
    caller: str | None, required: bool
) -> None:
    for bad_chain in (SOUND, chain("*")):
        assert check_source(caller, UNREADABLE, required=required, chain=bad_chain) is (
            Refusal.NETWORK
        )


def test_an_address_without_a_list_accepts_any_source_unless_a_list_is_required() -> None:
    assert check_source(SUPPLIER, NONE, required=False, chain=SOUND) is None
    assert check_source("192.0.2.1", NONE, required=False, chain=SOUND) is None
    assert check_source(SUPPLIER, NONE, required=True, chain=SOUND) is Refusal.NETWORK


def test_a_caller_inside_the_list_passes_and_one_outside_it_is_refused_for_its_network() -> None:
    assert check_source(SUPPLIER, LISTED, required=False, chain=SOUND) is None
    assert check_source("2001:4860:4860:1::9", LISTED, required=True, chain=SOUND) is None
    assert check_source("::ffff:8.8.8.8", LISTED, required=False, chain=SOUND) is None
    assert check_source("8.8.4.4", LISTED, required=False, chain=SOUND) is Refusal.NETWORK
    assert check_source("2001:4860:4861::1", LISTED, required=False, chain=SOUND) is (
        Refusal.NETWORK
    )


@pytest.mark.parametrize("trusted", ["*", "10.0.0.0/8", "172.31.0.0/16", "2001:db8::/32"])
def test_a_proxy_trust_wider_than_single_hosts_makes_the_address_untrustworthy(
    trusted: str,
) -> None:
    """Every address could be spoofed: a list that cannot be evaluated is refused, not passed."""
    wide = chain(trusted)

    assert wide.standing_faults() == (ChainFault.WIDE_TRUST,)
    assert check_source(SUPPLIER, LISTED, required=False, chain=wide) is Refusal.SOURCE_UNKNOWN
    # even a caller that is in the list: the address is not believed
    assert check_source("8.8.4.4", LISTED, required=True, chain=wide) is Refusal.SOURCE_UNKNOWN


@pytest.mark.parametrize("trusted", ["172.31.240.2", "172.31.240.2/32", "2001:db8::1/128"])
def test_single_hosts_are_a_trustworthy_proxy(trusted: str) -> None:
    assert chain(trusted).standing_faults() == ()
    assert check_source(SUPPLIER, LISTED, required=False, chain=chain(trusted)) is None


@pytest.mark.parametrize(
    "entry", ["/run/proxy.sock", "proxy.voorbeeld.nl", "172.31.240", "10.0.0.256"]
)
def test_an_entry_that_cannot_be_read_is_a_standing_fault_of_its_own_and_refuses_like_wide_trust(
    entry: str,
) -> None:
    """A trust list the server cannot read must not be skipped: who is trusted is unknown."""
    unreadable = chain("172.31.240.2", entry)

    assert unreadable.standing_faults() == (ChainFault.UNREADABLE_TRUST,)
    assert check_source(SUPPLIER, LISTED, required=False, chain=unreadable) is (
        Refusal.SOURCE_UNKNOWN
    )
    assert check_source(SUPPLIER, NONE, required=False, chain=unreadable) is None
    assert chain("*", entry).standing_faults() == (
        ChainFault.WIDE_TRUST,
        ChainFault.UNREADABLE_TRUST,
    )


@pytest.mark.parametrize("caller", ["172.31.240.2", "::ffff:172.31.240.2", "2001:db8:ffff::2"])
def test_a_caller_that_is_one_of_the_trusted_proxies_means_a_hop_did_not_hand_it_on(
    caller: str,
) -> None:
    behind = chain("172.31.240.2", "2001:db8:ffff::2")

    assert behind.fault_for(caller) is ChainFault.CALLER_IS_PROXY
    assert check_source(caller, LISTED, required=False, chain=behind) is Refusal.SOURCE_UNKNOWN
    assert behind.fault_for(SUPPLIER) is None


@pytest.mark.parametrize("caller", ["127.0.0.1", "::1", "10.1.2.3", "192.168.0.7", "fd00::7"])
def test_a_loopback_or_private_caller_while_the_public_address_is_public_is_not_believed(
    caller: str,
) -> None:
    private = NetworkList(("10.0.0.0/8", "::1/128"))

    assert SOUND.fault_for(caller) is ChainFault.HOP_NOT_HANDED_ON
    assert check_source(caller, private, required=False, chain=SOUND) is Refusal.SOURCE_UNKNOWN


def test_a_private_caller_is_fine_when_the_public_address_is_private_too() -> None:
    internal = chain("10.9.9.9", public=False)

    assert internal.fault_for("10.1.2.3") is None
    assert (
        check_source("10.1.2.3", NetworkList(("10.1.0.0/16",)), required=False, chain=internal)
        is None
    )


def test_forwarded_headers_this_call_carried_and_the_server_ignored_make_it_untrustworthy() -> None:
    """Decided per call: nothing standing, so it is no state a finding or another call can see."""
    ignored = chain("172.31.240.2", headers_ignored=True)

    assert ignored.standing_faults() == ()
    assert ignored.fault_for(SUPPLIER) is ChainFault.UNTRUSTED_HOP
    assert check_source(SUPPLIER, LISTED, required=False, chain=ignored) is Refusal.SOURCE_UNKNOWN
    assert check_source(SUPPLIER, LISTED, required=False, chain=chain("172.31.240.2")) is None
    assert chain("*", headers_ignored=True).standing_faults() == (ChainFault.WIDE_TRUST,)


@pytest.mark.parametrize("caller", [None, "", "testclient", "not-an-address"])
def test_a_caller_without_an_address_is_not_believed_when_a_list_applies(
    caller: str | None,
) -> None:
    assert SOUND.fault_for(caller) is ChainFault.NO_ADDRESS
    assert check_source(caller, LISTED, required=False, chain=SOUND) is Refusal.SOURCE_UNKNOWN


@pytest.mark.parametrize(
    "bad",
    [
        chain("*"),
        chain("172.31.240.2", "/run/proxy.sock"),
        chain("172.31.240.2", headers_ignored=True),
        chain("172.31.240.2", public=True),
    ],
)
def test_an_address_without_a_list_is_never_affected_by_the_proxy_chain(bad: ProxyChain) -> None:
    for caller in (SUPPLIER, "172.31.240.2", "127.0.0.1", None):
        assert check_source(caller, NONE, required=False, chain=bad) is None


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


@pytest.mark.parametrize(
    ("sources", "required", "state"),
    [
        (NONE, False, SourceState.UNRESTRICTED),
        (NONE, True, SourceState.REQUIRED_MISSING),
        (LISTED, False, SourceState.RESTRICTED),
        (LISTED, True, SourceState.RESTRICTED),
        (UNREADABLE, False, SourceState.UNREADABLE),
        (UNREADABLE, True, SourceState.UNREADABLE),
    ],
)
def test_the_state_of_an_address_is_never_silent(
    sources: NetworkList, required: bool, state: SourceState
) -> None:
    assert source_state(sources, required=required) is state


@pytest.mark.parametrize(
    ("caller", "trusted", "fault"),
    [
        ("172.31.240.2", "172.31.240.2", ChainFault.CALLER_IS_PROXY),
        ("10.1.2.3", "172.31.240.2", ChainFault.HOP_NOT_HANDED_ON),
        (None, "172.31.240.2", ChainFault.NO_ADDRESS),
        (SUPPLIER, "*", ChainFault.WIDE_TRUST),
    ],
)
def test_the_judgement_names_the_fault_of_this_call_behind_a_source_unknown(
    caller: str | None, trusted: str, fault: ChainFault
) -> None:
    """The card counts the per-call faults by kind; only ``source_unknown`` has one."""
    judged = judge_source(caller, LISTED, required=False, chain=chain(trusted))

    assert judged == (Refusal.SOURCE_UNKNOWN, fault)


def test_the_judgement_names_no_fault_for_the_other_answers() -> None:
    assert judge_source("8.8.4.4", LISTED, required=False, chain=SOUND) == (Refusal.NETWORK, None)
    assert judge_source(SUPPLIER, LISTED, required=False, chain=SOUND) == (None, None)
    assert judge_source(SUPPLIER, NONE, required=True, chain=SOUND) == (Refusal.NETWORK, None)
    assert judge_source(SUPPLIER, UNREADABLE, required=False, chain=SOUND) == (
        Refusal.NETWORK,
        None,
    )
