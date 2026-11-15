"""The worker's re-check of routing login domains (ADR 0083 point 12; abuse case B10)."""

from datetime import datetime, timedelta

import pytest

from app.domain.audit import Outcome
from app.domain.identity import PLATFORM
from app.domain.identity_providers import ClientAuth, OrganisationProvider
from app.domain.login_domain_watch import EVENT, DomainWatch
from app.domain.login_domains import DomainClaim, DomainState, route
from app.domain.system_log import SystemLog
from tests.fakes.clock import FixedClock
from tests.fakes.dns import FakeTxtResolver
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW
from tests.support.organisation_providers import ORGANISATIONS

INTERVAL = timedelta(hours=24)
GRACE = timedelta(days=7)
NAME = "_openplanner-challenge.acme-a.nl"
VALUE = "openplanner-domain-verification=tok"
PROVIDER = OrganisationProvider(
    1,
    "demo",
    "org-a",
    "Acme A",
    "https://login.org-a.example/.well-known/openid-configuration",
    "https://login.org-a.example",
    "openplanner",
    ClientAuth.PRIVATE_KEY_JWT,
    "openid profile email",
    "openplanner_entitlements",
    "openplanner",
    True,
    False,
    NOW,
    NOW,
)


@pytest.fixture
def dns() -> FakeTxtResolver:
    return FakeTxtResolver({NAME: (VALUE,)})


def unit(state: DomainState, checked_at: datetime | None) -> InMemoryUnitOfWork:
    data = State()
    data.organisations = {o.id: o for o in ORGANISATIONS}
    data.organisation_slugs = {o.id: o.slug for o in ORGANISATIONS}
    data.identity_providers = {PROVIDER.key: PROVIDER}
    data.login_domains = {
        1: DomainClaim(1, "org-a", "acme-a.nl", "tok", state, NOW, NOW, checked_at)
    }
    return InMemoryUnitOfWork(data)


def watch(uow: InMemoryUnitOfWork, clock: FixedClock, dns: FakeTxtResolver | None) -> DomainWatch:
    return DomainWatch(uow, clock, dns, SystemLog(uow, clock), lambda: INTERVAL, lambda: GRACE)


def stored(uow: InMemoryUnitOfWork) -> DomainClaim:
    return uow.state.login_domains[1]


def events(uow: InMemoryUnitOfWork) -> list[tuple[str, Outcome, str, str]]:
    return [(e.action, e.outcome, e.reason, e.detail) for e in uow.state.audit]


async def test_a_routing_domain_is_checked_only_when_due(
    clock: FixedClock, dns: FakeTxtResolver
) -> None:
    uow = unit(DomainState.VERIFIED, NOW)

    await watch(uow, clock, dns).run()
    clock.advance(INTERVAL)
    await watch(uow, clock, dns).run()

    assert dns.asked == [NAME]
    assert stored(uow).checked_at == NOW + INTERVAL
    assert events(uow) == []


async def test_a_lost_record_fails_then_lapses_and_stops_routing(
    clock: FixedClock, dns: FakeTxtResolver
) -> None:
    """B10: a domain that changes owner stops routing after the grace period."""
    uow = unit(DomainState.VERIFIED, None)
    dns.records[NAME] = ()

    failing = await watch(uow, clock, dns).run()
    clock.advance(GRACE + INTERVAL)
    lapsed = await watch(uow, clock, dns).run()
    clock.advance(INTERVAL)
    again = await watch(uow, clock, dns).run()

    assert [c.state for c in failing] == [DomainState.FAILING]
    assert [c.state for c in lapsed] == [DomainState.LAPSED]
    assert again == []
    assert route("jan@acme-a.nl", [stored(uow)], "openplanner.internal") == PLATFORM
    detail = "key:org-a,domain:acme-a.nl"
    assert events(uow) == [
        (EVENT, Outcome.FAILURE, "failing", detail),
        (EVENT, Outcome.FAILURE, "lapsed", detail),
    ]


async def test_a_restored_record_verifies_again(clock: FixedClock, dns: FakeTxtResolver) -> None:
    uow = unit(DomainState.FAILING, None)

    restored = await watch(uow, clock, dns).run()

    assert [c.state for c in restored] == [DomainState.VERIFIED]
    assert events(uow)[0][1] is Outcome.SUCCESS


async def test_an_outage_of_the_resolver_lapses_nothing(clock: FixedClock) -> None:
    uow = unit(DomainState.VERIFIED, None)
    clock.advance(timedelta(days=60))

    await watch(uow, clock, FakeTxtResolver({NAME: None})).run()
    await watch(uow, clock, None).run()

    assert stored(uow).state is DomainState.VERIFIED
    assert events(uow) == []


async def test_pending_and_lapsed_domains_are_left_alone(
    clock: FixedClock, dns: FakeTxtResolver
) -> None:
    for state in (DomainState.PENDING, DomainState.LAPSED):
        uow = unit(state, None)

        await watch(uow, clock, dns).run()

        assert stored(uow).state is state
    assert dns.asked == []
