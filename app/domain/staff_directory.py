"""Managing staff in the bundled identity provider: the rules, not the transport (ADR 0095).

The portal may create, change and disable people in a provider that offers the
``StaffDirectory`` capability (``app.domain.ports``). What it writes is the entitlement claim
of ADR 0049, and two rules keep that safe:

* an admin only ever changes values inside their own ``staff.manage`` scope, and only grants
  roles that scope may hold (``refusal_of``): nobody grants more than they hold themselves;
* everything outside that scope, or that this installation cannot read, is carried over
  untouched (``plan_entitlements``), so editing a colleague who also works elsewhere never
  removes the other grants.

    wish (Grants) ──refusal_of──► allowed?  ──plan_entitlements(existing)──► new claim values
"""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.authorization import REACH, Grant, Reach, Role, Scope
from app.domain.entitlements import Directory, entitlement_value, parse_entitlements

CREATED_EVENT = "staff.directory.created"
"""Audit event: a person was created at the identity provider."""

ENTITLEMENTS_EVENT = "staff.directory.entitlements_set"
"""Audit event: the roles and places of a person were changed."""

ENABLED_EVENT = "staff.directory.enabled_set"
"""Audit event: a person was enabled or disabled."""

PASSWORD_RESET_EVENT = "staff.directory.password_reset_sent"  # noqa: S105 - an event name
"""Audit event: a password reset or invitation link was sent."""

ADMIN_URL_PATTERN = re.compile(
    r"^(?P<host>https?://[^/]+(?:/[^/]+)*?)/admin/realms/(?P<realm>[^/]+)$"
)
"""``KEYCLOAK_ADMIN_URL``: the realm's admin API, for example
``http://keycloak:8080/admin/realms/openplanner``."""

_BUILT_IN = {role.value: role for role in Role}


class StaffAction(StrEnum):
    """What a person is asked to do through an emailed link; fixed, never configurable."""

    UPDATE_PASSWORD = "update_password"  # noqa: S105 - an action name
    VERIFY_EMAIL = "verify_email"


class DirectoryRefusal(StrEnum):
    """Why a requested grant is not written; a code, never user-facing text."""

    UNSUPPORTED_ROLE = "unsupported_role"
    SHAPE = "shape"
    ABOVE_OWN_RIGHTS = "above_own_rights"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN_TARGET = "unknown_target"


class DirectoryError(Exception):
    """The identity provider's directory refused or could not be reached.

    Attributes:
        code: A short code (``unreachable``, ``refused``, ``not_found``, ``conflict``); never
            personal data and never the provider's own message.
    """

    def __init__(self, code: str) -> None:
        """Remember the code."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DirectoryUser:
    """A person as the provider holds them.

    Attributes:
        id: The provider's stable identifier, equal to the ``sub`` of their ID token.
        username: Login name.
        email: Address the invitation and reset links go to.
        first_name: Given name.
        last_name: Family name.
        enabled: Whether the person can log in.
        entitlements: Their entitlement claim values, as stored.
    """

    id: str
    username: str = field(repr=False)
    email: str = field(repr=False)
    first_name: str = field(repr=False)
    last_name: str = field(repr=False)
    enabled: bool = True
    entitlements: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NewStaffMember:
    """What the portal asks the provider to create."""

    email: str = field(repr=False)
    first_name: str = field(repr=False)
    last_name: str = field(repr=False)


def refusal_of(scope: Scope, grant: Grant, *, superuser: bool) -> DirectoryRefusal | None:
    """Return why ``grant`` may not be written by someone with ``scope``, or ``None``.

    Args:
        scope: The acting admin's ``staff.manage`` scope.
        grant: The role and target wished for.
        superuser: Whether the acting admin holds the superuser role; only they grant it.
    """
    role = _BUILT_IN.get(grant.role)
    if role is None:
        return DirectoryRefusal.UNSUPPORTED_ROLE
    reach = REACH[role]
    wanted = {
        Reach.EVERYTHING: (False, False),
        Reach.ORGANISATIONS: (True, False),
        Reach.GROUPS: (True, True),
    }[reach]
    if (grant.organisation is not None, grant.group is not None) != wanted:
        return DirectoryRefusal.SHAPE
    if role is Role.SUPERUSER:
        return None if superuser and scope.everything else DirectoryRefusal.ABOVE_OWN_RIGHTS
    if not scope.contains(grant.organisation, grant.group):
        return DirectoryRefusal.OUT_OF_SCOPE
    return None


def in_scope(grant: Grant, scope: Scope) -> bool:
    """Whether an existing grant is one this admin may replace."""
    if grant.organisation is None:
        return scope.everything
    return scope.contains(grant.organisation, grant.group)


def plan_entitlements(
    existing: Iterable[str],
    wanted: Collection[Grant],
    scope: Scope,
    prefix: str,
    directory: Directory,
) -> list[str]:
    """Return the claim values a person should hold after the admin's change.

    Values that name a grant inside ``scope`` are replaced by ``wanted``; every other value
    (another organisation, another prefix, anything unreadable) is kept as it is.

    Raises:
        DirectoryError: With code ``unknown_target`` when a wanted grant names an organisation
            or group this installation does not know.
    """
    kept: list[str] = []
    for value in existing:
        parsed = parse_entitlements([value], prefix, directory)
        if len(parsed.grants) == 1 and in_scope(next(iter(parsed.grants)), scope):
            continue
        kept.append(value)
    added = sorted({_value_of(grant, prefix, directory) for grant in wanted})
    return kept + [value for value in added if value not in kept]


def _value_of(grant: Grant, prefix: str, directory: Directory) -> str:
    """Return the claim value of ``grant`` by the IdP values of its organisation and group."""
    if grant.organisation is None:
        return entitlement_value(prefix, grant.role)
    organisation = directory.organisation_by_slug(grant.organisation)
    if organisation is None:
        raise DirectoryError("unknown_target")
    if grant.group is None:
        return entitlement_value(prefix, grant.role, organisation.idp_key)
    group = directory.group_by_slug(grant.group)
    if group is None or group.organisation != grant.organisation:
        raise DirectoryError("unknown_target")
    return entitlement_value(prefix, grant.role, organisation.idp_key, group.idp_key)
