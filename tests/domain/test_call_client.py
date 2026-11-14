"""Which client a participant gets into the call, and whether that is our page (ADR 0123)."""

import itertools

import pytest

from app.domain.call_client import (
    DEFAULT_JOIN_CLIENT,
    EmbeddedBlocker,
    allows_other_way,
    call_client,
    embedded_blocker,
    other_way_offer,
    redirect_client,
    uses_call_page,
)
from app.domain.call_page import OtherWayOffer
from app.domain.calls import join_link
from app.domain.scheduling import JoinClient
from tests.support.domain import make_group

CLIENTS = [*JoinClient, None]
LINK = "https://acme.webex.com/meet/abc?x=1"


@pytest.mark.parametrize(
    ("service", "flow", "enabled"), list(itertools.product(JoinClient, CLIENTS, (True, False)))
)
def test_the_flow_wins_over_the_service_and_embedded_acts_only_with_the_switch_on(
    service: JoinClient, flow: JoinClient | None, enabled: bool
) -> None:
    group = make_group(join_client=service)
    chosen = flow or service

    got = call_client(group, flow, platform_enabled=enabled)

    if chosen is JoinClient.EMBEDDED and not enabled:
        assert got is DEFAULT_JOIN_CLIENT is JoinClient.BROWSER
    else:
        assert got is chosen
    assert uses_call_page(group, flow, platform_enabled=enabled) is (
        enabled and chosen is JoinClient.EMBEDDED
    )


def test_a_service_that_chose_nothing_is_the_default_client() -> None:
    group = make_group()

    assert group.join_client is JoinClient.BROWSER
    assert call_client(group, platform_enabled=True) is JoinClient.BROWSER
    assert not uses_call_page(group, platform_enabled=True)


def test_the_switch_is_the_kill_switch_whatever_the_service_says() -> None:
    group = make_group(join_client=JoinClient.EMBEDDED)

    assert uses_call_page(group, platform_enabled=True)
    assert not uses_call_page(group, platform_enabled=False)
    assert not uses_call_page(group, JoinClient.EMBEDDED, platform_enabled=False)


@pytest.mark.parametrize("client", JoinClient)
def test_every_client_has_a_redirect_the_link_builder_knows(client: JoinClient) -> None:
    shaped = join_link(LINK, redirect_client(client))

    assert shaped is not None
    if client is JoinClient.CHOICE:
        assert shaped == LINK
    else:  # browser, and embedded as its way around our page
        assert shaped.endswith("x=1&launchApp=true")
    assert join_link(LINK, client) == shaped  # the builder takes embedded itself, no fall-through


@pytest.mark.parametrize(
    ("own", "default", "expected"),
    [
        (None, "hidden", OtherWayOffer.HIDDEN),
        (None, "always", OtherWayOffer.ALWAYS),
        (None, "nonsense", OtherWayOffer.HIDDEN),
        (OtherWayOffer.ON_FAILURE, "always", OtherWayOffer.ON_FAILURE),
        (OtherWayOffer.HIDDEN, "always", OtherWayOffer.HIDDEN),
    ],
)
def test_the_services_option_wins_and_none_follows_the_organisation(
    own: OtherWayOffer | None, default: str, expected: OtherWayOffer
) -> None:
    assert other_way_offer(make_group(embedded_call_webex_option=own), default) is expected


def test_only_hidden_keeps_the_platforms_address_closed() -> None:
    assert [allows_other_way(offer) for offer in OtherWayOffer] == [False, True, True]


@pytest.mark.parametrize(
    ("enabled", "passed", "expected"),
    [
        (False, False, EmbeddedBlocker.PLATFORM_SWITCH),
        (False, True, EmbeddedBlocker.PLATFORM_SWITCH),
        (True, False, EmbeddedBlocker.GUEST_CHECK),
        (True, True, None),
    ],
)
def test_embedded_needs_the_switch_and_a_guest_the_check_minted(
    enabled: bool, passed: bool, expected: EmbeddedBlocker | None
) -> None:
    assert embedded_blocker(platform_enabled=enabled, guest_check_passed=passed) is expected
