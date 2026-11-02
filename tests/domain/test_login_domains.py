"""Domains that route a login, and the reserved platform suffix (ADR 0083, ADR 0106 point 9)."""

from datetime import timedelta

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.identity import PLATFORM
from app.domain.login_domains import (
    MAX_DOMAINS_PER_PROVIDER,
    DomainClaim,
    DomainState,
    checked,
    covers,
    domain_of,
    normalise_domain,
    refusal_of_claim,
    reserved,
    route,
    suffix_conflicts,
)
from tests.support.domain import NOW

SUFFIX = "openplanner.internal"
GRACE = timedelta(days=7)


def claim(
    domain: str, provider: str = "gem-a", state: DomainState = DomainState.VERIFIED
) -> DomainClaim:
    return DomainClaim(None, provider, domain, "tok", state, NOW)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Gemeente.NL", "gemeente.nl"),
        (" mail.gemeente.nl. ", "mail.gemeente.nl"),
        ("bücher.de", "xn--bcher-kva.de"),
    ],
)
def test_domains_are_normalised(text: str, expected: str) -> None:
    assert normalise_domain(text) == expected


@pytest.mark.parametrize("text", ["", "nl", "-a.nl", "a..nl", "a b.nl", "x" * 64 + ".nl", "@a.nl"])
def test_what_is_not_a_domain_is_refused(text: str) -> None:
    with pytest.raises(DomainError) as refused:
        normalise_domain(text)
    assert refused.value.code is ErrorCode.IDP_DOMAIN_INVALID


def test_a_domain_covers_itself_and_its_subdomains_only() -> None:
    assert covers("gemeente.nl", "gemeente.nl")
    assert covers("gemeente.nl", "mail.gemeente.nl")
    assert not covers("gemeente.nl", "andergemeente.nl")
    assert not covers("mail.gemeente.nl", "gemeente.nl")


def test_the_suffix_is_reserved_both_ways() -> None:
    assert reserved("openplanner.internal", SUFFIX)
    assert reserved("demo.openplanner.internal", SUFFIX)
    assert reserved("internal.", SUFFIX) is False
    assert reserved("example.nl", "staff.example.nl")


def test_a_claim_is_refused_on_the_suffix_on_a_taken_domain_and_past_the_limit() -> None:
    many = [claim(f"d{i}.nl") for i in range(MAX_DOMAINS_PER_PROVIDER)]

    assert refusal_of_claim("demo.openplanner.internal", "gem-a", [], SUFFIX) is (
        ErrorCode.IDP_DOMAIN_RESERVED
    )
    assert refusal_of_claim(
        "gemeente.nl", "gem-b", [claim("gemeente.nl", state=DomainState.PENDING)], SUFFIX
    ) is (ErrorCode.IDP_DOMAIN_TAKEN)
    assert refusal_of_claim("extra.nl", "gem-a", many, SUFFIX) is ErrorCode.IDP_DOMAIN_LIMIT
    assert refusal_of_claim("extra.nl", "gem-b", many, SUFFIX) is None


def test_the_suffix_cannot_overlap_a_claimed_domain() -> None:
    claims = [claim("example.nl")]

    assert suffix_conflicts("staff.example.nl", claims)
    assert suffix_conflicts("example.nl", claims)
    assert not suffix_conflicts(SUFFIX, claims)


def test_a_found_record_verifies_and_a_lost_one_lapses_after_the_grace() -> None:
    pending = claim("gemeente.nl", state=DomainState.PENDING)
    record = (pending.record_value,)

    verified = checked(pending, record, NOW, GRACE)
    failing = checked(verified, (), NOW + timedelta(days=1), GRACE)
    still = checked(failing, (), NOW + timedelta(days=7), GRACE)
    lapsed = checked(failing, (), NOW + timedelta(days=8), GRACE)
    back = checked(failing, record, NOW + timedelta(days=2), GRACE)

    assert (verified.state, verified.verified_at) == (DomainState.VERIFIED, NOW)
    assert (failing.state, failing.missing_since) == (DomainState.FAILING, NOW + timedelta(days=1))
    assert still.state is DomainState.FAILING
    assert lapsed.state is DomainState.LAPSED
    assert (back.state, back.missing_since) == (DomainState.VERIFIED, None)


def test_a_failed_lookup_changes_nothing_but_the_time() -> None:
    verified = claim("gemeente.nl")

    after = checked(verified, None, NOW + timedelta(days=30), GRACE)

    assert after.state is DomainState.VERIFIED
    assert after.checked_at == NOW + timedelta(days=30)


def test_a_pending_claim_without_record_stays_pending() -> None:
    assert checked(claim("a.nl", state=DomainState.PENDING), (), NOW, GRACE).state is (
        DomainState.PENDING
    )


def test_the_record_is_published_under_the_challenge_name() -> None:
    found = claim("gemeente.nl")

    assert found.record_name == "_openplanner-challenge.gemeente.nl"
    assert found.record_value == "openplanner-domain-verification=tok"


def test_the_domain_of_an_address() -> None:
    assert domain_of("Jan@Gemeente.NL") == "gemeente.nl"
    assert domain_of("jan") == ""


def test_routing_prefers_the_reserved_suffix_then_the_longest_routing_claim() -> None:
    claims = [
        claim("gemeente.nl", "gem-a"),
        claim("team.gemeente.nl", "gem-b"),
        claim("pending.nl", "gem-c", DomainState.PENDING),
        claim("lapsed.nl", "gem-c", DomainState.LAPSED),
        claim("failing.nl", "gem-d", DomainState.FAILING),
    ]

    assert route("jan@gemeente.nl", claims, SUFFIX) == "gem-a"
    assert route("jan@x.team.gemeente.nl", claims, SUFFIX) == "gem-b"
    assert route("jan@pending.nl", claims, SUFFIX) == PLATFORM
    assert route("jan@lapsed.nl", claims, SUFFIX) == PLATFORM
    assert route("jan@failing.nl", claims, SUFFIX) == "gem-d"
    assert route("jan.jansen@demo.openplanner.internal", claims, SUFFIX) == PLATFORM
    assert route("jan@elders.nl", claims, SUFFIX) == PLATFORM
    assert route("super", claims, SUFFIX) == PLATFORM
