"""What is left to do before an organisation's own identity provider is used (ADR 0083).

Three pure answers for the authentication page of an organisation, so nobody has to know the
order of the work by heart:

* ``setup_steps``: the five steps from the host switch to a verified domain, each done, still
  to do or blocked by an earlier one;
* ``setup_warnings``: states that look finished but never route a login, such as a provider
  that is on without a verified domain;
* ``check_route``: where a given address would go and why, with the same rules as
  ``login_domains.route`` and ``IdentityProviderService.login_provider``.

Nothing here reads a store, a clock or a request. The address passed to ``check_route`` is
personal data: the result keeps its domain only, never the address itself (point 13, B12).

    steps:   host_switch → register → enable → domain → verify
    address: no domain │ reserved suffix │ no claim │ claim not verified │ host switch off
             │ provider off │ provider
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.identity import PLATFORM
from app.domain.identity_providers import OrganisationProvider
from app.domain.login_domains import DomainClaim, DomainState, covers, domain_of, route

STEP_KEYS = ("host_switch", "register", "enable", "domain", "verify")
"""The steps in the order the work is done; the page translates each key."""


class StepState(StrEnum):
    """Where one set-up step stands."""

    DONE = "done"
    TODO = "todo"
    BLOCKED = "blocked"
    """An earlier step has to be done first."""


@dataclass(frozen=True)
class SetupStep:
    """One step of the set-up and where it stands."""

    key: str
    state: StepState


@dataclass(frozen=True)
class SetupWarning:
    """A state that never routes a login although it looks done.

    Attributes:
        code: Stable code the page translates (``enabled_without_domain``,
            ``domain_provider_off``, ``domain_pending``, ``domain_failing``,
            ``domain_lapsed``).
        provider: The provider key it is about.
        domain: The domain it is about, ``""`` when it is about the provider as a whole.
    """

    code: str
    provider: str
    domain: str


def setup_steps(
    *,
    routing: bool,
    providers: Sequence[OrganisationProvider],
    claims: Iterable[DomainClaim],
) -> list[SetupStep]:
    """Return the five steps with their state, for one organisation's providers and domains.

    Args:
        routing: Whether the host allows organisation providers (``ORGANISATION_IDPS_ENABLED``).
        providers: The organisation's providers.
        claims: The domains claimed by those providers.
    """
    claim_list = list(claims)
    registered = bool(providers)
    done = {
        "host_switch": routing,
        "register": registered,
        "enable": any(p.enabled for p in providers),
        "domain": bool(claim_list),
        "verify": any(c.state.routes for c in claim_list),
    }
    needs = {
        "host_switch": True,
        "register": True,
        "enable": registered,
        "domain": registered,
        "verify": bool(claim_list),
    }
    return [
        SetupStep(
            key,
            StepState.DONE if done[key] else StepState.TODO if needs[key] else StepState.BLOCKED,
        )
        for key in STEP_KEYS
    ]


def setup_warnings(
    providers: Sequence[OrganisationProvider], claims: Iterable[DomainClaim]
) -> list[SetupWarning]:
    """Return what keeps a provider from being chosen although it is registered.

    A provider that is on needs a domain that routes, a routing domain needs a provider that is
    on, and a domain that is claimed but not verified, failing or lapsed is called out.
    """
    claim_list = list(claims)
    found: list[SetupWarning] = []
    for provider in providers:
        own = [c for c in claim_list if c.provider == provider.key]
        routes = [c for c in own if c.state.routes]
        if provider.enabled and not routes:
            found.append(SetupWarning("enabled_without_domain", provider.key, ""))
        if not provider.enabled:
            found.extend(
                SetupWarning("domain_provider_off", provider.key, c.domain) for c in routes
            )
        found.extend(
            SetupWarning(_STATE_CODES[c.state], provider.key, c.domain)
            for c in own
            if c.state in _STATE_CODES
        )
    return found


_STATE_CODES = {
    DomainState.PENDING: "domain_pending",
    DomainState.FAILING: "domain_failing",
    DomainState.LAPSED: "domain_lapsed",
}
"""Warning per domain state that is not fully verified."""


class RouteReason(StrEnum):
    """Why an address goes where it goes."""

    PROVIDER = "provider"
    NO_DOMAIN = "no_domain"
    """The identifier has no ``@domain`` part, such as a bare user name."""
    RESERVED_SUFFIX = "reserved_suffix"
    """The domain is the bundled provider's suffix, which always belongs to the platform."""
    NO_DOMAIN_MATCH = "no_domain_match"
    """No provider claims the domain."""
    DOMAIN_NOT_VERIFIED = "domain_not_verified"
    """A provider claims the domain, but the claim does not route (pending or lapsed)."""
    PROVIDER_OFF = "provider_off"
    """A provider routes the domain but is switched off."""
    HOST_SWITCH_OFF = "host_switch_off"
    """The host does not allow organisation providers, so everything goes to the platform."""


@dataclass(frozen=True)
class RouteCheck:
    """Where an address goes and why; it holds the domain, never the address.

    Attributes:
        target: The provider key it goes to, ``PLATFORM`` (``""``) for the platform provider.
        reason: Why.
        domain: The domain part of what was typed, ``""`` without one.
        provider: The provider the domain matches, also when it is not used (off, not verified,
            host switch); ``""`` when none matches.
        foreign: The match belongs to another organisation; the page names no provider then.
    """

    target: str
    reason: RouteReason
    domain: str
    provider: str = ""
    foreign: bool = False


def check_route(
    address: str,
    providers: Sequence[OrganisationProvider],
    claims: Iterable[DomainClaim],
    suffix: str,
    *,
    routing: bool,
) -> RouteCheck:
    """Return where a login with ``address`` goes and why, as the login page itself decides.

    Args:
        address: What a person would type on the login page.
        providers: Every provider of the installation (a domain belongs to one of them).
        claims: Every claimed domain of the installation.
        suffix: The bundled provider's reserved suffix (``bundled_idp_login_suffix``).
        routing: ``ORGANISATION_IDPS_ENABLED``.
    """
    claim_list = list(claims)
    domain = domain_of(address)
    if not domain:
        return RouteCheck(PLATFORM, RouteReason.NO_DOMAIN, "")
    if covers(suffix, domain):
        return RouteCheck(PLATFORM, RouteReason.RESERVED_SUFFIX, domain)
    key = route(address, claim_list, suffix)
    if key == PLATFORM:
        unverified = [c for c in claim_list if covers(c.domain, domain)]
        if unverified:
            best = max(unverified, key=lambda c: len(c.domain))
            return RouteCheck(PLATFORM, RouteReason.DOMAIN_NOT_VERIFIED, domain, best.provider)
        return RouteCheck(PLATFORM, RouteReason.NO_DOMAIN_MATCH, domain)
    if not routing:
        return RouteCheck(PLATFORM, RouteReason.HOST_SWITCH_OFF, domain, key)
    chosen = next((p for p in providers if p.key == key), None)
    if chosen is None or not chosen.enabled:
        return RouteCheck(PLATFORM, RouteReason.PROVIDER_OFF, domain, key)
    return RouteCheck(key, RouteReason.PROVIDER, domain, key)
