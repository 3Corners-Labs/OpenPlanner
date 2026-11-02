"""E-mail domains that route a login to an organisation provider (ADR 0083 points 10 to 13).

A provider claims a domain; a TXT record proves the organisation controls it, the way Control
Hub, Entra ID and Google Workspace do. A verified domain also covers its subdomains. One domain
belongs to at most one provider on the installation, verified or not.

The platform suffix of the bundled provider (``bundled_idp_login_suffix``, ADR 0106 point 9) is
reserved: no organisation provider claims it or a subdomain of it, the suffix cannot be set to
a domain a provider claims, and an address on it always goes to the platform provider.

    claim(domain) ──refusal_of_claim──► pending ──TXT found──► verified
    verified ──TXT missing──► failing (still routes) ──grace over──► lapsed (stops routing)
    address ──route──► reserved suffix? platform : longest routing claim : platform
"""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode
from app.domain.identity import PLATFORM

EMAIL_NOT_VERIFIED = "email_not_verified"
"""Refusal: an organisation provider's ID token lacks ``email_verified: true`` (point 15)."""
EMAIL_DOMAIN_MISMATCH = "email_domain_mismatch"
"""Refusal: the address is not on one of the provider's routing domains (point 15, B3)."""

MAX_DOMAINS_PER_PROVIDER = 20
"""Fixed in ADR 0083 point 11."""
DEFAULT_GRACE = timedelta(days=7)
"""The default of ``idp_domain_grace_days``."""
MAX_DOMAIN_LENGTH = 253
"""The longest domain name DNS allows (RFC 1035)."""
CHALLENGE_LABEL = "_openplanner-challenge"
"""The TXT record sits at ``_openplanner-challenge.<domain>`` (point 10)."""
CHALLENGE_PREFIX = "openplanner-domain-verification="
"""The TXT value starts with this, followed by the claim's token."""

MIN_LABELS = 2
"""A name and a top-level label: a bare ``nl`` or ``internal`` is never claimed."""

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class DomainState(StrEnum):
    """Where a claimed domain stands.

    ``FAILING`` is a verified domain whose record went missing: it keeps routing for the grace
    period, then becomes ``LAPSED`` and stops (point 12).
    """

    PENDING = "pending"
    VERIFIED = "verified"
    FAILING = "failing"
    LAPSED = "lapsed"

    @property
    def routes(self) -> bool:
        """Whether a login on this domain goes to the claiming provider."""
        return self in (DomainState.VERIFIED, DomainState.FAILING)


@dataclass(frozen=True)
class DomainClaim:
    """One domain claimed by one organisation provider.

    Attributes:
        id: Database id, ``None`` before it is stored.
        provider: The provider key.
        domain: The domain, normalised (lower case, ASCII, no trailing dot).
        token: The random value the TXT record must carry; not a secret, never personal.
        state: Pending, verified, failing or lapsed.
        created_at: When it was claimed.
        verified_at: When the record was first found.
        checked_at: When the record was last looked up.
        missing_since: When a verified domain's record was first found missing.
    """

    id: int | None
    provider: str
    domain: str
    token: str
    state: DomainState
    created_at: datetime
    verified_at: datetime | None = None
    checked_at: datetime | None = None
    missing_since: datetime | None = None

    @property
    def record_name(self) -> str:
        """Where the TXT record must be published."""
        return f"{CHALLENGE_LABEL}.{self.domain}"

    @property
    def record_value(self) -> str:
        """What the TXT record must say."""
        return f"{CHALLENGE_PREFIX}{self.token}"


def normalise_domain(text: str) -> str:
    """Return ``text`` as a lower-case ASCII domain of at least two labels.

    An internationalised domain is converted to its ``xn--`` form.

    Raises:
        DomainError: ``idp_domain_invalid`` for anything that is not a domain name.
    """
    value = text.strip().rstrip(".").lower()
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        raise DomainError(ErrorCode.IDP_DOMAIN_INVALID, "domain") from None
    labels = value.split(".")
    if (
        len(value) > MAX_DOMAIN_LENGTH
        or len(labels) < MIN_LABELS
        or not all(_LABEL.fullmatch(label) for label in labels)
    ):
        raise DomainError(ErrorCode.IDP_DOMAIN_INVALID, "domain")
    return value


def covers(domain: str, candidate: str) -> bool:
    """Return whether ``domain`` covers ``candidate``: the same, or a subdomain of it."""
    return candidate == domain or candidate.endswith(f".{domain}")


def overlaps(first: str, second: str) -> bool:
    """Return whether either domain covers the other."""
    return covers(first, second) or covers(second, first)


def reserved(domain: str, suffix: str) -> bool:
    """Return whether ``domain`` overlaps the platform suffix (ADR 0106 point 9).

    Both directions count: a claim under the suffix would capture platform user names, and a
    claim above it would let the organisation's DNS speak for them.
    """
    return overlaps(domain, suffix)


def refusal_of_claim(
    domain: str, provider: str, claims: Collection[DomainClaim], suffix: str
) -> ErrorCode | None:
    """Return why ``provider`` may not claim ``domain``, or ``None`` (points 10 and 11)."""
    if reserved(domain, suffix):
        return ErrorCode.IDP_DOMAIN_RESERVED
    if any(c.domain == domain for c in claims):
        return ErrorCode.IDP_DOMAIN_TAKEN
    if sum(c.provider == provider for c in claims) >= MAX_DOMAINS_PER_PROVIDER:
        return ErrorCode.IDP_DOMAIN_LIMIT
    return None


def suffix_conflicts(suffix: str, claims: Iterable[DomainClaim]) -> bool:
    """Return whether the suffix may not be set: a provider claims an overlapping domain."""
    return any(overlaps(c.domain, suffix) for c in claims)


def checked(
    claim: DomainClaim, records: Collection[str] | None, now: datetime, grace: timedelta
) -> DomainClaim:
    """Return ``claim`` after one lookup of its TXT record.

    ``records`` is ``None`` when the lookup itself failed (no answer, time-out): the state
    then stays as it was, so a resolver outage never lapses a domain.
    """
    if records is None:
        return replace(claim, checked_at=now)
    if claim.record_value in records:
        return replace(
            claim,
            state=DomainState.VERIFIED,
            verified_at=claim.verified_at or now,
            checked_at=now,
            missing_since=None,
        )
    if claim.state is DomainState.PENDING:
        return replace(claim, checked_at=now)
    since = claim.missing_since or now
    state = DomainState.LAPSED if now - since >= grace else DomainState.FAILING
    return replace(claim, state=state, checked_at=now, missing_since=since)


def domain_of(address: str) -> str:
    """Return the domain part of an address or user name, lower case ('' without one)."""
    _, at, domain = address.strip().rpartition("@")
    return domain.rstrip(".").lower() if at else ""


def route(address: str, claims: Iterable[DomainClaim], suffix: str) -> str:
    """Return the key of the provider a login with ``address`` goes to (point 13).

    The reserved suffix goes to the platform provider first; otherwise the longest routing
    claim that covers the domain wins; no match is the platform provider.
    """
    domain = domain_of(address)
    if not domain or covers(suffix, domain):
        return PLATFORM
    matches = [c for c in claims if c.state.routes and covers(c.domain, domain)]
    if not matches:
        return PLATFORM
    return max(matches, key=lambda c: len(c.domain)).provider


def refusal_of_identity(
    email: str | None, verified: bool | None, provider: str, claims: Iterable[DomainClaim]
) -> str | None:
    """Return why an organisation provider may not sign this person in, or ``None``.

    The ID token must carry ``email`` with ``email_verified`` true, on a domain the provider
    itself routes (point 15): no provider speaks for a domain it does not own.
    """
    if verified is not True:
        return EMAIL_NOT_VERIFIED
    domain = domain_of(email or "")
    owned = any(
        c.provider == provider and c.state.routes and covers(c.domain, domain) for c in claims
    )
    return None if domain and owned else EMAIL_DOMAIN_MISMATCH
