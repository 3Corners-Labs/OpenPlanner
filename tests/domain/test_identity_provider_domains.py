"""Login domains of an organisation provider (ADR 0083 points 10 to 12, ADR 0106 point 9).

Includes the abuse case B9 (claiming a domain the organisation does not own) and the reserved
platform suffix of the bundled provider.
"""

from datetime import timedelta

import pytest

from app.domain.admin import AdminService
from app.domain.errors import DomainError, ErrorCode
from app.domain.identity_provider_service import (
    DOMAIN_CLAIMED_EVENT,
    DOMAIN_FAILED_EVENT,
    DOMAIN_REMOVED_EVENT,
    DOMAIN_VERIFIED_EVENT,
    IdentityProviderService,
)
from app.domain.login_domains import DomainState
from tests.fakes.clock import FixedClock
from tests.fakes.dns import FakeTxtResolver
from tests.fakes.identity_providers import FakeDiscoveryFetcher, FakeSigningKeys
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, ORG_ADMIN, SUPER
from tests.support.organisation_providers import PLATFORM_DISCOVERY, draft


@pytest.fixture
def dns() -> FakeTxtResolver:
    return FakeTxtResolver()


@pytest.fixture
async def domains(
    uow: InMemoryUnitOfWork,
    clock: FixedClock,
    discovery: FakeDiscoveryFetcher,
    keys: FakeSigningKeys,
    changed: list[str],
    dns: FakeTxtResolver,
) -> IdentityProviderService:
    service = IdentityProviderService(
        uow,
        clock,
        AdminService(uow, clock),
        discovery,
        keys,
        limit=lambda: 2,
        platform_discovery_url=lambda: PLATFORM_DISCOVERY,
        on_change=changed.append,
        resolver=dns,
        login_suffix=lambda: "openplanner.internal",
        grace=lambda: timedelta(days=7),
    )
    await service.create(SUPER, "demo", draft(), None)
    return service


def actions(uow: InMemoryUnitOfWork) -> list[str]:
    return [e.action for e in uow.state.audit if e.action.startswith("idp.domain")]


async def test_a_claimed_domain_waits_for_its_record(
    domains: IdentityProviderService, uow: InMemoryUnitOfWork
) -> None:
    claim = await domains.claim_domain(SUPER, "demo", "org-a", " Acme-A.nl ")

    detail = await domains.provider(AUDITOR, "demo", "org-a")
    assert claim.domain == "acme-a.nl"
    assert claim.state is DomainState.PENDING
    assert len(claim.token) >= 32  # noqa: PLR2004 - 32 random bytes, URL-safe
    assert [d.domain for d in detail.domains] == ["acme-a.nl"]
    assert actions(uow) == [DOMAIN_CLAIMED_EVENT]


@pytest.mark.parametrize(
    ("domain", "code"),
    [
        ("openplanner.internal", ErrorCode.IDP_DOMAIN_RESERVED),
        ("demo.openplanner.internal", ErrorCode.IDP_DOMAIN_RESERVED),
        ("not a domain", ErrorCode.IDP_DOMAIN_INVALID),
    ],
)
async def test_the_platform_suffix_and_garbage_are_refused(
    domains: IdentityProviderService, domain: str, code: ErrorCode
) -> None:
    with pytest.raises(DomainError) as refused:
        await domains.claim_domain(SUPER, "demo", "org-a", domain)

    assert refused.value.code is code


async def test_a_domain_belongs_to_one_provider(domains: IdentityProviderService) -> None:
    await domains.create(SUPER, "demo", draft(key="org-b", discovery_url=OTHER), None)
    await domains.claim_domain(SUPER, "demo", "org-a", "acme-a.nl")

    with pytest.raises(DomainError) as refused:
        await domains.claim_domain(SUPER, "demo", "org-b", "acme-a.nl")

    assert refused.value.code is ErrorCode.IDP_DOMAIN_TAKEN


async def test_only_the_superuser_claims_a_domain(domains: IdentityProviderService) -> None:
    """B13 and B9: an organisation admin cannot point a domain at a provider."""
    with pytest.raises(DomainError) as refused:
        await domains.claim_domain(ORG_ADMIN, "demo", "org-a", "acme-a.nl")

    assert refused.value.code is ErrorCode.NOT_PERMITTED


async def test_the_record_verifies_the_domain_and_its_loss_is_audited(
    domains: IdentityProviderService,
    uow: InMemoryUnitOfWork,
    dns: FakeTxtResolver,
    clock: FixedClock,
    changed: list[str],
) -> None:
    claim = await domains.claim_domain(SUPER, "demo", "org-a", "acme-a.nl")
    assert claim.id is not None
    unproven = await domains.verify_domain(SUPER, "demo", "org-a", claim.id)
    dns.records[claim.record_name] = (claim.record_value,)
    verified = await domains.verify_domain(SUPER, "demo", "org-a", claim.id)
    dns.records[claim.record_name] = ()
    clock.advance(timedelta(days=8))
    failing = await domains.verify_domain(SUPER, "demo", "org-a", claim.id)

    assert unproven.state is DomainState.PENDING
    assert verified.state is DomainState.VERIFIED
    assert failing.state is DomainState.FAILING
    assert dns.asked[0] == "_openplanner-challenge.acme-a.nl"
    assert actions(uow) == [DOMAIN_CLAIMED_EVENT, DOMAIN_VERIFIED_EVENT, DOMAIN_FAILED_EVENT]
    assert changed.count("org-a") >= 2  # noqa: PLR2004 - created, then verified


async def test_removing_a_domain_releases_it(
    domains: IdentityProviderService, uow: InMemoryUnitOfWork
) -> None:
    claim = await domains.claim_domain(SUPER, "demo", "org-a", "acme-a.nl")
    assert claim.id is not None

    await domains.remove_domain(SUPER, "demo", "org-a", claim.id)

    assert (await domains.provider(SUPER, "demo", "org-a")).domains == []
    assert actions(uow) == [DOMAIN_CLAIMED_EVENT, DOMAIN_REMOVED_EVENT]
    with pytest.raises(DomainError) as refused:
        await domains.remove_domain(SUPER, "demo", "org-a", claim.id)
    assert refused.value.code is ErrorCode.IDP_DOMAIN_NOT_FOUND


async def test_the_suffix_may_not_overlap_a_claimed_domain(
    domains: IdentityProviderService,
) -> None:
    await domains.claim_domain(SUPER, "demo", "org-a", "example.nl")

    assert await domains.suffix_conflicts("staff.example.nl")
    assert not await domains.suffix_conflicts("openplanner.internal")


async def test_deleting_the_provider_releases_its_domains(
    domains: IdentityProviderService,
) -> None:
    await domains.claim_domain(SUPER, "demo", "org-a", "acme-a.nl")

    await domains.delete(SUPER, "demo", "org-a")

    assert not await domains.suffix_conflicts("acme-a.nl")


OTHER = "https://login.org-b.example/.well-known/openid-configuration"
