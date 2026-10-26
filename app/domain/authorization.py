"""The authorisation matrix: the single source of rights for every layer (ADR 0014).

Roles come from the identity provider (ADR 0013). Each permission applies at one level:

    GLOBAL        something about the admin portal itself (dashboard, own rights, the
                  holiday calendars every organisation can reuse)
    ORGANISATION  something of one organisation (audit log, new groups, integrations)
    GROUP         something of one group (settings, opening hours, texts, bookings)

A role grants a permission at the levels it covers:

    superuser    every organisation and group (a scope that matches everything, not a bypass)
    org_admin    the organisations in the principal's organisation claim, and their groups
    group_admin  the groups in the principal's group claim

``decide`` answers one question for routes; ``scope_for`` turns the same rules into a
``Scope`` that the database adapter applies to listings. Both are derived from ``MATRIX``,
so what a list shows is exactly what a route allows.

Citizens never log in. Their rights depend on holding a management link and on the change
deadline; ``CITIZEN_ACTIONS`` documents them next to the admin matrix.

Machines (contact-center bots, ADR 0023) never get a role either. An API client holds OAuth
scopes (``API_SCOPES``) within one organisation, optionally narrowed to some of its groups;
``decide_client`` is its counterpart of ``decide``.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """Admin roles (HANDOVER B8)."""

    SUPERUSER = "superuser"
    ORG_ADMIN = "org_admin"
    GROUP_ADMIN = "group_admin"


class Level(StrEnum):
    """What a permission applies to."""

    GLOBAL = "global"
    ORGANISATION = "organisation"
    GROUP = "group"


class Permission(StrEnum):
    """Everything an admin can be allowed to do."""

    DASHBOARD_VIEW = "dashboard.view"
    RIGHTS_VIEW = "rights.view"
    MATRIX_VIEW = "matrix.view"
    ORGANISATION_MANAGE = "organisation.manage"
    AUDIT_VIEW = "audit.view"
    INTEGRATIONS_MANAGE = "integrations.manage"
    BRANDING_MANAGE = "branding.manage"
    HOLIDAYS_MANAGE = "holidays.manage"
    HOLIDAYS_ASSIGN = "holidays.assign"
    GROUP_CREATE = "group.create"
    GROUP_VIEW = "group.view"
    GROUP_EDIT = "group.edit"
    SCHEDULE_MANAGE = "schedule.manage"
    TEXTS_MANAGE = "texts.manage"
    REMINDERS_MANAGE = "reminders.manage"
    BOOKINGS_VIEW = "bookings.view"
    CLOSURE_MANAGE = "closure.manage"
    ORGANISATION_CLOSURE = "organisation.closure"
    API_CLIENTS_MANAGE = "api_clients.manage"


LEVELS: dict[Permission, Level] = {
    Permission.DASHBOARD_VIEW: Level.GLOBAL,
    Permission.RIGHTS_VIEW: Level.GLOBAL,
    Permission.MATRIX_VIEW: Level.GLOBAL,
    Permission.ORGANISATION_MANAGE: Level.GLOBAL,
    Permission.HOLIDAYS_MANAGE: Level.GLOBAL,
    Permission.AUDIT_VIEW: Level.ORGANISATION,
    Permission.INTEGRATIONS_MANAGE: Level.ORGANISATION,
    Permission.BRANDING_MANAGE: Level.ORGANISATION,
    Permission.HOLIDAYS_ASSIGN: Level.ORGANISATION,
    Permission.GROUP_CREATE: Level.ORGANISATION,
    Permission.GROUP_VIEW: Level.GROUP,
    Permission.GROUP_EDIT: Level.GROUP,
    Permission.SCHEDULE_MANAGE: Level.GROUP,
    Permission.TEXTS_MANAGE: Level.GROUP,
    Permission.REMINDERS_MANAGE: Level.GROUP,
    Permission.BOOKINGS_VIEW: Level.GROUP,
    Permission.CLOSURE_MANAGE: Level.GROUP,
    Permission.ORGANISATION_CLOSURE: Level.ORGANISATION,
    Permission.API_CLIENTS_MANAGE: Level.GLOBAL,
}

_EVERY_ADMIN = frozenset(
    {Permission.DASHBOARD_VIEW, Permission.RIGHTS_VIEW, Permission.MATRIX_VIEW}
)

MATRIX: dict[Role, frozenset[Permission]] = {
    # HANDOVER §6: everything, including organisations, IdP, SMTP and WxCC credentials.
    Role.SUPERUSER: frozenset(Permission),
    # HANDOVER §6: own organisation: groups, rules, overrides, texts, bookings of its groups;
    # ADR 0025: which of the superuser's holiday calendars its groups follow.
    Role.ORG_ADMIN: _EVERY_ADMIN
    | {
        Permission.AUDIT_VIEW,
        Permission.HOLIDAYS_ASSIGN,
        Permission.GROUP_CREATE,
        Permission.GROUP_VIEW,
        Permission.GROUP_EDIT,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.REMINDERS_MANAGE,
        Permission.BOOKINGS_VIEW,
        # ADR 0024: close one group or every group of the organisation at once.
        Permission.CLOSURE_MANAGE,
        Permission.ORGANISATION_CLOSURE,
    },
    # HANDOVER §6: own groups: rules, overrides, mail texts, view bookings.
    Role.GROUP_ADMIN: _EVERY_ADMIN
    | {
        Permission.GROUP_VIEW,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.BOOKINGS_VIEW,
        # ADR 0024: the team itself can close in an emergency and handle its bookings.
        Permission.CLOSURE_MANAGE,
    },
}


@dataclass(frozen=True)
class CitizenAction:
    """Something a citizen can do without logging in, and the condition for it."""

    key: str
    condition: str


CITIZEN_ACTIONS: tuple[CitizenAction, ...] = (
    CitizenAction("citizen.availability_view", "public"),
    CitizenAction("citizen.book", "public"),
    CitizenAction("citizen.booking_view", "management_link"),
    CitizenAction("citizen.booking_change", "management_link_before_deadline"),
    CitizenAction("citizen.booking_cancel", "management_link_before_deadline"),
    # ADR 0024: a booking on hold can be moved or cancelled until it is resolved.
    CitizenAction("citizen.suspended_booking_change", "management_link_while_suspended"),
)


class ApiScope(StrEnum):
    """OAuth scopes of the machine API (ADR 0023), granted per API client by the superuser."""

    AVAILABILITY_READ = "availability:read"


API_SCOPES: dict[ApiScope, Level] = {
    ApiScope.AVAILABILITY_READ: Level.GROUP,
}
"""Every API scope with the level it applies to; documented next to the admin matrix."""


class Reason(StrEnum):
    """Why a decision was made; logged and audited, never shown with internal detail."""

    SUPERUSER = "superuser"
    ROLE = "role"
    ORGANISATION = "organisation"
    GROUP = "group"
    NO_ROLE = "no_role"
    NOT_PERMITTED = "not_permitted"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class Decision:
    """The outcome of ``decide``."""

    allowed: bool
    reason: Reason


@dataclass(frozen=True)
class Principal:
    """A logged-in admin as the identity provider describes them.

    Attributes:
        subject: Stable identifier from the identity provider; used in logs and audit.
        display_name: For the page header only; never logged.
        roles: Roles from the role claim.
        organisations: Organisation slugs from the organisation claim (org_admin).
        groups: Group slugs from the group claim (group_admin).
    """

    subject: str
    display_name: str = field(repr=False)
    roles: frozenset[Role]
    organisations: frozenset[str]
    groups: frozenset[str]


@dataclass(frozen=True)
class ClientGrant:
    """What an API client may reach (ADR 0023).

    Attributes:
        client_id: Public identifier of the client; used in logs and audit.
        organisation: Slug of the one organisation the client belongs to.
        groups: Slugs of the groups it is narrowed to; empty means every group of the
            organisation.
        scopes: The OAuth scopes it holds.
    """

    client_id: str
    organisation: str
    groups: frozenset[str]
    scopes: frozenset[ApiScope]


@dataclass(frozen=True)
class Target:
    """What a permission is checked against. Build with the class methods."""

    level: Level
    organisation: str | None = None
    group: str | None = None

    @classmethod
    def everything(cls) -> "Target":
        """The target of a global permission."""
        return cls(Level.GLOBAL)

    @classmethod
    def for_organisation(cls, organisation: str) -> "Target":
        """An organisation, by slug."""
        return cls(Level.ORGANISATION, organisation)

    @classmethod
    def for_group(cls, group: str, organisation: str) -> "Target":
        """A group, by slug, with the slug of its organisation."""
        return cls(Level.GROUP, organisation, group)


@dataclass(frozen=True)
class Scope:
    """Which organisations and groups a listing may show; applied in SQL by the adapter."""

    everything: bool = False
    organisations: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        """Whether the scope matches nothing."""
        return not (self.everything or self.organisations or self.groups)

    def contains(self, organisation: str | None, group: str | None) -> bool:
        """Return whether a group (with its organisation) falls inside the scope."""
        return (
            self.everything
            or (organisation is not None and organisation in self.organisations)
            or (group is not None and group in self.groups)
        )


def _granting_roles(principal: Principal, permission: Permission) -> set[Role]:
    """Return the principal's roles that include ``permission``."""
    return {role for role in principal.roles if permission in MATRIX[role]}


def decide(principal: Principal, permission: Permission, target: Target) -> Decision:
    """Decide whether ``principal`` may use ``permission`` on ``target``.

    Raises:
        ValueError: If the target's level does not match the permission (a programming error).
    """
    if target.level is not LEVELS[permission]:
        raise ValueError(f"{permission} needs a {LEVELS[permission]} level target")
    if not principal.roles:
        return Decision(False, Reason.NO_ROLE)
    roles = _granting_roles(principal, permission)
    if not roles:
        return Decision(False, Reason.NOT_PERMITTED)
    if Role.SUPERUSER in roles:
        return Decision(True, Reason.SUPERUSER)
    if target.level is Level.GLOBAL:
        return Decision(True, Reason.ROLE)
    if Role.ORG_ADMIN in roles and target.organisation in principal.organisations:
        return Decision(True, Reason.ORGANISATION)
    if (
        target.level is Level.GROUP
        and Role.GROUP_ADMIN in roles
        and target.group in principal.groups
    ):
        return Decision(True, Reason.GROUP)
    return Decision(False, Reason.OUT_OF_SCOPE)


def decide_client(grant: ClientGrant, api_scope: ApiScope, target: Target) -> Decision:
    """Decide whether an API client may use ``api_scope`` on ``target``.

    The organisation must match; a group must also be one of the client's groups when it is
    narrowed to some. There is no superuser equivalent for machines.

    Raises:
        ValueError: If the target's level does not match the scope (a programming error).
    """
    if target.level is not API_SCOPES[api_scope]:
        raise ValueError(f"{api_scope} needs a {API_SCOPES[api_scope]} level target")
    if api_scope not in grant.scopes:
        return Decision(False, Reason.NOT_PERMITTED)
    if target.organisation != grant.organisation:
        return Decision(False, Reason.OUT_OF_SCOPE)
    if not grant.groups:
        return Decision(True, Reason.ORGANISATION)
    if target.group in grant.groups:
        return Decision(True, Reason.GROUP)
    return Decision(False, Reason.OUT_OF_SCOPE)


def scope_for(principal: Principal, permission: Permission) -> Scope:
    """Return what listings may show for ``permission`` (the database side of ``decide``).

    Raises:
        ValueError: For a global permission, which has no organisation or group scope.
    """
    level = LEVELS[permission]
    if level is Level.GLOBAL:
        raise ValueError(f"{permission} is a global permission and has no scope")
    roles = _granting_roles(principal, permission)
    if Role.SUPERUSER in roles:
        return Scope(everything=True)
    organisations = principal.organisations if Role.ORG_ADMIN in roles else frozenset()
    groups = principal.groups if Role.GROUP_ADMIN in roles and level is Level.GROUP else frozenset()
    return Scope(organisations=organisations, groups=groups)


def permissions_for(principal: Principal) -> list[tuple[Permission, Scope | None]]:
    """Return the principal's permissions with their scope (``None`` for global ones).

    Used by the "My rights" page. Permissions whose scope is empty are left out.
    """
    rights: list[tuple[Permission, Scope | None]] = []
    for permission in Permission:
        if not _granting_roles(principal, permission):
            continue
        if LEVELS[permission] is Level.GLOBAL:
            rights.append((permission, None))
            continue
        scope = scope_for(principal, permission)
        if not scope.is_empty:
            rights.append((permission, scope))
    return rights


def roles_with(permission: Permission) -> Iterable[Role]:
    """Return the roles that include ``permission``, in matrix order (for reports)."""
    return [role for role in Role if permission in MATRIX[role]]
