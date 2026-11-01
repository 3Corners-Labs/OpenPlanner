"""Entitlements: role and scope paired in one claim value (ADR 0049).

The identity provider sends one value per right, ``<prefix>:<role>:<org>[/<group>]`` or
``<prefix>:superuser``. ``parse_entitlements`` turns such values into ``Grant``s against a
``Directory`` (the role keys with their reach, and the IdP values of organisations and groups)
and refuses what it does not recognise, each refusal with a reason code. Nothing is guessed,
trimmed or case-folded; values without the prefix are ignored, because the claim may be filled
from a shared directory attribute.

Until an installation switches to entitlements (``ClaimMode``), the three legacy claims of
ADR 0013 still carry the rights: ``legacy_grants`` makes grants of them, and ``is_ambiguous``
is the safety net of ADR 0049 point 14 (two roles of one reach over more than one target is
the cross product nobody granted, so the login is refused). ``resolve`` combines both for the
mode at hand, so the login flow and every request make a ``Principal`` the same way::

    LoginClaims ──resolve(mode, prefix, directory, catalogue)──► Resolution
                   legacy / shadow: legacy_grants   (shadow also parses and compares)
                   entitlements:    parse_entitlements
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.authorization import (
    REACH,
    Grant,
    Principal,
    Reach,
    Role,
    RoleCatalogue,
)

PREFIX_PATTERN = r"[a-z][a-z0-9-]{1,30}"
"""Grammar of the entitlement prefix (a setting): separates installations on one IdP."""

IDP_KEY_PATTERN = r"[a-z0-9][a-z0-9-]{0,62}"
"""Grammar of an organisation's or group's IdP value (``idp_key``, ADR 0049 point 4)."""

ROLE_KEY_PATTERN = r"[a-z][a-z0-9_.]{1,39}"
"""Grammar of the role segment: a built-in key or ``custom.<id>`` (the dot is the one addition
to ADR 0049 point 2, because custom role keys carry one)."""

_PREFIX = re.compile(PREFIX_PATTERN)
_IDP_KEY = re.compile(IDP_KEY_PATTERN)
_VALUE = re.compile(
    rf"^(?P<role>{ROLE_KEY_PATTERN})(?::(?P<org>{IDP_KEY_PATTERN})(?:/(?P<group>{IDP_KEY_PATTERN}))?)?$"
)
"""The part after ``<prefix>:``; the reach of the role decides which form is valid."""

MAX_SESSION_GRANTS = 60
"""Recognised values a session may hold (the cookie is 4 kB); more refuses the login, fail
closed like the claim limit of point 6. Fixed: a bound of the session mechanism, not a policy."""


class ClaimMode(StrEnum):
    """Where rights come from (``OIDC_CLAIM_MODE``, ADR 0049 point 13)."""

    LEGACY = "legacy"
    SHADOW = "shadow"
    ENTITLEMENTS = "entitlements"


class RefusalReason(StrEnum):
    """Why a value with the prefix gave no grant (ADR 0049 point 5); a code, never text."""

    MALFORMED = "malformed"
    UNKNOWN_ROLE = "unknown_role"
    UNKNOWN_ORGANISATION = "unknown_organisation"
    UNKNOWN_GROUP = "unknown_group"
    GROUP_NOT_IN_ORGANISATION = "group_not_in_organisation"
    SCOPE_NOT_ALLOWED_FOR_ROLE = "scope_not_allowed_for_role"


def is_prefix(value: str) -> bool:
    """Return whether ``value`` can be the entitlement prefix."""
    return _PREFIX.fullmatch(value) is not None


def is_idp_key(value: str) -> bool:
    """Return whether ``value`` can be an organisation's or group's IdP value."""
    return _IDP_KEY.fullmatch(value) is not None


@dataclass(frozen=True)
class OrganisationRef:
    """An organisation as the directory knows it: its slug and its IdP value."""

    slug: str
    idp_key: str


@dataclass(frozen=True)
class GroupRef:
    """A group as the directory knows it, with the slug of its organisation."""

    slug: str
    idp_key: str
    organisation: str


@dataclass(frozen=True)
class Directory:
    """What a claim value can name: role keys with their reach, organisations and groups.

    Loaded once per request by the database adapter (``Transaction.get_directory``); pure
    data, so parsing stays domain code and testable without a database.
    """

    roles: Mapping[str, Reach]
    organisations: tuple[OrganisationRef, ...] = ()
    groups: tuple[GroupRef, ...] = ()

    @classmethod
    def of(
        cls,
        catalogue: RoleCatalogue,
        organisations: Iterable[OrganisationRef] = (),
        groups: Iterable[GroupRef] = (),
    ) -> "Directory":
        """Build a directory from the role catalogue and the known organisations and groups."""
        return cls(catalogue.reach_by_key(), tuple(organisations), tuple(groups))

    def organisation_by_idp_key(self, key: str) -> OrganisationRef | None:
        """Return the organisation whose IdP value is ``key``."""
        return next((o for o in self.organisations if o.idp_key == key), None)

    def organisation_by_slug(self, slug: str) -> OrganisationRef | None:
        """Return the organisation with ``slug``."""
        return next((o for o in self.organisations if o.slug == slug), None)

    def groups_by_idp_key(self, key: str) -> list[GroupRef]:
        """Return every group whose IdP value is ``key`` (unique within an organisation)."""
        return [g for g in self.groups if g.idp_key == key]

    def group_by_slug(self, slug: str) -> GroupRef | None:
        """Return the group with ``slug``."""
        return next((g for g in self.groups if g.slug == slug), None)


@dataclass(frozen=True)
class Refusal:
    """One value with the prefix that gave no grant.

    Attributes:
        value: The value as received (prefix, role key, IdP values: never personal data).
        reason: Why.
        organisation: Slug of the organisation the value names, when that one exists; decides
            who may see the refusal (ADR 0049 point 8, A14).
    """

    value: str
    reason: RefusalReason
    organisation: str | None = None


@dataclass(frozen=True)
class ParsedEntitlements:
    """What ``parse_entitlements`` made of a claim."""

    grants: frozenset[Grant] = frozenset()
    refused: tuple[Refusal, ...] = ()
    ignored: int = 0
    """Values without the prefix: not ours, not a problem."""


def parse_entitlements(
    values: Iterable[str], prefix: str, directory: Directory
) -> ParsedEntitlements:
    """Return the grants and the refusals of entitlement ``values`` (ADR 0049 points 1–5).

    Exact match only: no case folding, no trimming, no wildcards. Each value is judged on its
    own; a refused value never affects another. The order of the refusals is that of the
    values, without duplicates.
    """
    head = f"{prefix}:"
    grants: set[Grant] = set()
    refused: list[Refusal] = []
    seen: set[str] = set()
    ignored = 0
    for value in values:
        if not value.startswith(head):
            ignored += 1
            continue
        if value in seen:
            continue
        seen.add(value)
        outcome = _parse_one(value[len(head) :], directory)
        if isinstance(outcome, Grant):
            grants.add(outcome)
        else:
            refused.append(Refusal(value, outcome[0], outcome[1]))
    return ParsedEntitlements(frozenset(grants), tuple(refused), ignored)


def _parse_one(rest: str, directory: Directory) -> Grant | tuple[RefusalReason, str | None]:
    """Judge the part after the prefix: a grant, or the reason it is refused."""
    found = _VALUE.match(rest)
    if found is None:
        return RefusalReason.MALFORMED, None
    role, org_key, group_key = found.group("role"), found.group("org"), found.group("group")
    reach = directory.roles.get(role)
    if reach is None:
        return RefusalReason.UNKNOWN_ROLE, None
    wanted = {Reach.EVERYTHING: (False, False), Reach.ORGANISATIONS: (True, False)}.get(
        reach, (True, True)
    )
    if (org_key is not None, group_key is not None) != wanted:
        organisation = directory.organisation_by_idp_key(org_key) if org_key else None
        return RefusalReason.SCOPE_NOT_ALLOWED_FOR_ROLE, organisation.slug if organisation else None
    if org_key is None:
        return Grant(role)
    organisation = directory.organisation_by_idp_key(org_key)
    if organisation is None:
        return RefusalReason.UNKNOWN_ORGANISATION, None
    if group_key is None:
        return Grant(role, organisation.slug)
    groups = directory.groups_by_idp_key(group_key)
    if not groups:
        return RefusalReason.UNKNOWN_GROUP, organisation.slug
    group = next((g for g in groups if g.organisation == organisation.slug), None)
    if group is None:
        return RefusalReason.GROUP_NOT_IN_ORGANISATION, organisation.slug
    return Grant(role, organisation.slug, group.slug)


def entitlement_value(
    prefix: str, role: str, organisation: str | None = None, group: str | None = None
) -> str:
    """Return the exact value the IdP must send for ``role`` on a target, by IdP values."""
    if organisation is None:
        return f"{prefix}:{role}"
    place = f"{organisation}/{group}" if group else organisation
    return f"{prefix}:{role}:{place}"


@dataclass(frozen=True)
class LoginClaims:
    """What the ID token said about an admin, before the directory gives it meaning.

    Extracted by the OIDC adapter and kept in the session; both the legacy claims (ADR 0013)
    and the entitlement values, so the mode can change between login and request.

    Attributes:
        subject: Stable identifier from the identity provider.
        display_name: For the page header only; never logged.
        roles: Built-in roles from the legacy role claim.
        organisations: Organisation slugs from the legacy organisation claim.
        groups: Group slugs from the legacy group claim.
        role_claims: Other values of the legacy role claim (custom roles link to them).
        entitlements: Values of the entitlement claim that carry the prefix.
        mfa_missing: The provider's policy is ``record`` and the ID token showed no second
            factor (ADR 0100); the login goes on and the route records a security event.
    """

    subject: str
    display_name: str = field(repr=False)
    roles: frozenset[Role] = frozenset()
    organisations: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()
    role_claims: frozenset[str] = frozenset()
    entitlements: frozenset[str] = frozenset()
    mfa_missing: bool = False


def _legacy_roles(claims: LoginClaims, catalogue: RoleCatalogue) -> list[tuple[str, Reach]]:
    """Return the role keys the legacy claims name, with their reach."""
    found = [(role.value, REACH[role]) for role in claims.roles]
    found += [(r.key, r.reach) for r in catalogue.by_claim_value(claims.role_claims)]
    return found


def legacy_grants(
    claims: LoginClaims, directory: Directory, catalogue: RoleCatalogue
) -> frozenset[Grant]:
    """Return the grants the three legacy claims amount to (ADR 0013, as before ADR 0049).

    Every role of organisation reach is granted for every organisation slug; every role of
    group reach for every group slug the directory knows (with its organisation). Unknown
    group slugs grant nothing. This is the cross product; ``is_ambiguous`` says when it must
    not be trusted.
    """
    grants: set[Grant] = set()
    for key, reach in _legacy_roles(claims, catalogue):
        if reach is Reach.EVERYTHING:
            grants.add(Grant(key))
        elif reach is Reach.ORGANISATIONS:
            grants |= {Grant(key, slug) for slug in claims.organisations}
        else:
            for slug in claims.groups:
                group = directory.group_by_slug(slug)
                if group is not None:
                    grants.add(Grant(key, group.organisation, slug))
    return frozenset(grants)


def is_ambiguous(claims: LoginClaims, catalogue: RoleCatalogue) -> bool:
    """Return whether the legacy claims hand out a right nobody gave (ADR 0049 point 14).

    Two or more roles of the same reach while the claim that scopes that reach has more than
    one value: the cross product then gives each role every target.
    """
    roles = _legacy_roles(claims, catalogue)
    for reach, targets in (
        (Reach.ORGANISATIONS, claims.organisations),
        (Reach.GROUPS, claims.groups),
    ):
        if len(targets) > 1 and sum(1 for _, r in roles if r is reach) > 1:
            return True
    return False


MAX_DETAIL = 255
"""What ``audit_events.detail`` holds (a storage limit, not a policy)."""


def mismatch_detail(legacy: frozenset[Grant], entitled: frozenset[Grant]) -> str | None:
    """Return what differs between the two sets of grants, or ``None`` when they agree.

    Keys only (role keys and slugs): ``legacy_only:org_admin:demo;entitlements_only:...``.
    When the keys would not fit the detail they are counted instead.
    """
    only_legacy = sorted(g.key for g in legacy - entitled)
    only_entitled = sorted(g.key for g in entitled - legacy)
    if not only_legacy and not only_entitled:
        return None
    detail = ";".join(
        f"{name}:{','.join(keys)}"
        for name, keys in (("legacy_only", only_legacy), ("entitlements_only", only_entitled))
        if keys
    )
    if len(detail) <= MAX_DETAIL:
        return detail
    return f"legacy_only:{len(only_legacy)};entitlements_only:{len(only_entitled)}"


@dataclass(frozen=True)
class Resolution:
    """What ``resolve`` made of a login's claims under the current mode.

    Attributes:
        principal: The admin with their grants (empty when the legacy claims are ambiguous).
        refused: Entitlement values refused (``shadow`` and ``entitlements`` modes).
        ambiguous: The legacy claims were ambiguous (``legacy`` and ``shadow`` modes).
        mismatch: In ``shadow`` mode, how the entitlement claim differs from the legacy claims.
        too_many: More recognised values than a session can hold.
    """

    principal: Principal
    refused: tuple[Refusal, ...] = ()
    ambiguous: bool = False
    mismatch: str | None = None
    too_many: bool = False


def resolve(
    claims: LoginClaims,
    mode: ClaimMode,
    prefix: str,
    directory: Directory,
    catalogue: RoleCatalogue,
) -> Resolution:
    """Turn ``claims`` into a ``Principal`` for ``mode`` (ADR 0049 point 13).

    The same function serves the login (which refuses on ``ambiguous`` and ``too_many`` and
    records ``refused`` and ``mismatch``) and every request (which only needs the principal).
    """
    parsed = (
        parse_entitlements(claims.entitlements, prefix, directory)
        if mode is not ClaimMode.LEGACY
        else ParsedEntitlements()
    )
    ambiguous = mode is not ClaimMode.ENTITLEMENTS and is_ambiguous(claims, catalogue)
    if mode is ClaimMode.ENTITLEMENTS:
        grants = parsed.grants
        mismatch = None
    else:
        grants = frozenset() if ambiguous else legacy_grants(claims, directory, catalogue)
        mismatch = mismatch_detail(grants, parsed.grants) if mode is ClaimMode.SHADOW else None
    principal = catalogue.holding(Principal(claims.subject, claims.display_name, grants))
    return Resolution(
        principal, parsed.refused, ambiguous, mismatch, len(grants) > MAX_SESSION_GRANTS
    )


def recognised_values(claims: LoginClaims, prefix: str, directory: Directory) -> frozenset[str]:
    """Return the entitlement values that yield a grant, for the session to keep."""
    head = f"{prefix}:"
    return frozenset(
        v
        for v in claims.entitlements
        if v.startswith(head) and isinstance(_parse_one(v[len(head) :], directory), Grant)
    )
