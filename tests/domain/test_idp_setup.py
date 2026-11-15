"""The set-up steps, the warnings and the route check of the authentication page (ADR 0083)."""

from dataclasses import replace

import pytest

from app.domain.identity import PLATFORM
from app.domain.identity_providers import ClientAuth, OrganisationProvider
from app.domain.idp_setup import (
    RouteReason,
    SetupWarning,
    StepState,
    check_route,
    setup_steps,
    setup_warnings,
)
from app.domain.login_domains import DomainClaim, DomainState
from app.domain.mfa import MfaPolicy
from tests.support.domain import NOW

SUFFIX = "openplanner.internal"


def provider(
    key: str = "org-a", *, enabled: bool = False, organisation: str = "demo"
) -> OrganisationProvider:
    return OrganisationProvider(
        None,
        organisation,
        key,
        key.title(),
        "https://idp.example/.well-known/openid-configuration",
        "https://idp.example",
        "openplanner",
        ClientAuth.PRIVATE_KEY_JWT,
        "openid email",
        "openplanner_entitlements",
        "openplanner",
        enabled,
        False,
        NOW,
        NOW,
        None,
        MfaPolicy.RECORD,
    )


def claim(
    domain: str = "acme.nl", key: str = "org-a", state: DomainState = DomainState.VERIFIED
) -> DomainClaim:
    return DomainClaim(None, key, domain, "tok", state, NOW)


def states(**kwargs: object) -> dict[str, StepState]:
    return {step.key: step.state for step in setup_steps(**kwargs)}  # type: ignore[arg-type]


def test_an_empty_organisation_starts_at_the_host_switch_and_the_registration() -> None:
    found = states(routing=False, providers=[], claims=[])

    assert found == {
        "host_switch": StepState.TODO,
        "register": StepState.TODO,
        "enable": StepState.BLOCKED,
        "domain": StepState.BLOCKED,
        "verify": StepState.BLOCKED,
    }


def test_every_step_is_done_when_the_chain_works() -> None:
    found = states(routing=True, providers=[provider(enabled=True)], claims=[claim()])

    assert set(found.values()) == {StepState.DONE}


def test_a_registered_provider_unblocks_the_next_steps() -> None:
    found = states(routing=True, providers=[provider()], claims=[])

    assert found["register"] is StepState.DONE
    assert found["enable"] is StepState.TODO
    assert found["domain"] is StepState.TODO
    assert found["verify"] is StepState.BLOCKED


def test_a_pending_domain_is_claimed_but_not_verified() -> None:
    found = states(
        routing=True, providers=[provider(enabled=True)], claims=[claim(state=DomainState.PENDING)]
    )

    assert found["domain"] is StepState.DONE
    assert found["verify"] is StepState.TODO


def test_a_lapsed_domain_is_not_verified_any_more() -> None:
    found = states(
        routing=True, providers=[provider(enabled=True)], claims=[claim(state=DomainState.LAPSED)]
    )

    assert found["verify"] is StepState.TODO


def test_a_provider_that_is_on_without_a_routing_domain_is_warned_about() -> None:
    found = setup_warnings([provider(enabled=True)], [])

    assert found == [SetupWarning("enabled_without_domain", "org-a", "")]


def test_a_routing_domain_on_a_provider_that_is_off_is_warned_about() -> None:
    found = setup_warnings([provider()], [claim()])

    assert found == [SetupWarning("domain_provider_off", "org-a", "acme.nl")]


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (DomainState.PENDING, "domain_pending"),
        (DomainState.FAILING, "domain_failing"),
        (DomainState.LAPSED, "domain_lapsed"),
    ],
)
def test_a_domain_that_is_not_fully_verified_is_warned_about(state: DomainState, code: str) -> None:
    found = setup_warnings([provider(enabled=True)], [claim(state=state)])

    assert SetupWarning(code, "org-a", "acme.nl") in found


def test_a_healthy_set_up_has_no_warnings() -> None:
    assert setup_warnings([provider(enabled=True)], [claim()]) == []


def check(address: str, *, routing: bool = True, **kw: object):
    providers = kw.get("providers", [provider(enabled=True)])
    claims = kw.get("claims", [claim()])
    return check_route(address, providers, claims, SUFFIX, routing=routing)  # type: ignore[arg-type]


def test_an_address_on_a_verified_domain_goes_to_the_provider() -> None:
    found = check("ada@acme.nl")

    assert (found.target, found.reason, found.domain) == ("org-a", RouteReason.PROVIDER, "acme.nl")


def test_a_subdomain_goes_to_the_provider_too() -> None:
    assert check("ada@mail.acme.nl").target == "org-a"


def test_an_address_on_another_domain_goes_to_the_platform() -> None:
    found = check("ada@other.nl")

    assert (found.target, found.reason) == (PLATFORM, RouteReason.NO_DOMAIN_MATCH)


def test_a_user_name_without_a_domain_goes_to_the_platform() -> None:
    assert check("ada").reason is RouteReason.NO_DOMAIN


def test_the_reserved_suffix_goes_to_the_platform() -> None:
    found = check("ada@demo.openplanner.internal")

    assert (found.target, found.reason) == (PLATFORM, RouteReason.RESERVED_SUFFIX)


def test_a_domain_that_is_not_verified_yet_goes_to_the_platform_and_says_why() -> None:
    found = check("ada@acme.nl", claims=[claim(state=DomainState.PENDING)])

    assert (found.target, found.reason, found.provider) == (
        PLATFORM,
        RouteReason.DOMAIN_NOT_VERIFIED,
        "org-a",
    )


def test_a_provider_that_is_off_sends_the_address_to_the_platform() -> None:
    found = check("ada@acme.nl", providers=[provider()])

    assert (found.target, found.reason, found.provider) == (
        PLATFORM,
        RouteReason.PROVIDER_OFF,
        "org-a",
    )


def test_the_host_switch_sends_everything_to_the_platform_and_still_names_the_match() -> None:
    found = check("ada@acme.nl", routing=False)

    assert (found.target, found.reason, found.provider) == (
        PLATFORM,
        RouteReason.HOST_SWITCH_OFF,
        "org-a",
    )


def test_the_longest_domain_wins() -> None:
    found = check(
        "ada@team.acme.nl",
        providers=[provider("org-a", enabled=True), provider("org-b", enabled=True)],
        claims=[claim("acme.nl", "org-a"), claim("team.acme.nl", "org-b")],
    )

    assert found.target == "org-b"


def test_the_result_never_carries_the_address() -> None:
    found = check("ada.lovelace@acme.nl")

    assert "ada" not in repr(found)
    assert replace(found).domain == "acme.nl"
