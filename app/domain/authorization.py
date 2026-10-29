"""The authorisation matrix: the single source of rights for every layer (ADR 0014).

Roles come from the identity provider (ADR 0013). Each permission applies at one level:

    GLOBAL        something about the admin portal itself (dashboard, own rights, the
                  holiday calendars every organisation can reuse, updates of the installation)
    ORGANISATION  something of one organisation (new groups, integrations, branding)
    GROUP         something of one group (settings, opening hours, texts, bookings, the
                  audit log of its events)

A role grants a permission within its reach (``REACH``):

    superuser    everything: every organisation and group (a scope, not a bypass)
    org_admin    organisations: those in the principal's organisation claim, and their groups
    auditor      organisations, read-only and without personal data (ADR 0039)
    group_admin  groups: those in the principal's group claim

Some permissions are locked to the superuser (``LOCKED_PERMISSIONS``); some show citizens'
personal data (``PII_PERMISSIONS``). The matrix page and the documentation mark both.

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
    AUDITOR = "auditor"


class Reach(StrEnum):
    """Which organisations and groups a role covers, taken from the principal's claims."""

    EVERYTHING = "everything"
    ORGANISATIONS = "organisations"
    GROUPS = "groups"


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
    AUDIT_EXPORT = "audit.export"
    DIAGNOSTICS_MANAGE = "diagnostics.manage"
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
    BOOKINGS_MANAGE = "bookings.manage"
    CLOSURE_MANAGE = "closure.manage"
    ORGANISATION_CLOSURE = "organisation.closure"
    API_CLIENTS_MANAGE = "api_clients.manage"
    SYSTEM_UPDATE = "system.update"
    SYSTEM_MAINTENANCE = "system.maintenance"
    RETENTION_VIEW = "retention.view"
    STAFF_VIEW = "staff.view"
    EMBEDDING_MANAGE = "embedding.manage"


LEVELS: dict[Permission, Level] = {
    Permission.DASHBOARD_VIEW: Level.GLOBAL,
    Permission.RIGHTS_VIEW: Level.GLOBAL,
    Permission.MATRIX_VIEW: Level.GLOBAL,
    Permission.ORGANISATION_MANAGE: Level.GLOBAL,
    Permission.HOLIDAYS_MANAGE: Level.GLOBAL,
    Permission.INTEGRATIONS_MANAGE: Level.ORGANISATION,
    Permission.BRANDING_MANAGE: Level.ORGANISATION,
    Permission.HOLIDAYS_ASSIGN: Level.ORGANISATION,
    Permission.GROUP_CREATE: Level.ORGANISATION,
    Permission.AUDIT_VIEW: Level.GROUP,  # ADR 0031: one log, exact per level
    # ADR 0040: the same log as a CSV file, for compliance work outside the portal.
    Permission.AUDIT_EXPORT: Level.GROUP,
    # ADR 0040: debug events for one group or organisation; superuser only (PO, 2026-10-29).
    Permission.DIAGNOSTICS_MANAGE: Level.GLOBAL,
    Permission.GROUP_VIEW: Level.GROUP,
    Permission.GROUP_EDIT: Level.GROUP,
    Permission.SCHEDULE_MANAGE: Level.GROUP,
    Permission.TEXTS_MANAGE: Level.GROUP,
    Permission.REMINDERS_MANAGE: Level.GROUP,
    Permission.BOOKINGS_VIEW: Level.GROUP,
    # ADR 0044: a planner moves, cancels or accepts a shortfall per booking or slot.
    Permission.BOOKINGS_MANAGE: Level.GROUP,
    Permission.CLOSURE_MANAGE: Level.GROUP,
    Permission.ORGANISATION_CLOSURE: Level.ORGANISATION,
    Permission.API_CLIENTS_MANAGE: Level.GLOBAL,
    # ADR 0029: update or restore the installation itself.
    Permission.SYSTEM_UPDATE: Level.GLOBAL,
    # ADR 0038: switch maintenance mode of the whole installation on or off.
    Permission.SYSTEM_MAINTENANCE: Level.GLOBAL,
    # ADR 0039: per group, whether personal data is removed on time (counts only).
    Permission.RETENTION_VIEW: Level.ORGANISATION,
    # ADR 0046: who uses the portal; group level so a group admin sees their own team.
    Permission.STAFF_VIEW: Level.GROUP,
    # ADR 0035: on which websites the organisation's groups may be embedded.
    Permission.EMBEDDING_MANAGE: Level.ORGANISATION,
}

LOCKED_PERMISSIONS = frozenset(
    {
        Permission.ORGANISATION_MANAGE,
        Permission.HOLIDAYS_MANAGE,
        Permission.INTEGRATIONS_MANAGE,
        Permission.API_CLIENTS_MANAGE,
        Permission.SYSTEM_UPDATE,
        Permission.SYSTEM_MAINTENANCE,
        Permission.DIAGNOSTICS_MANAGE,
    }
)
"""ADR 0039: permissions that affect more than one organisation or touch secrets; superuser only."""

PII_PERMISSIONS = frozenset({Permission.BOOKINGS_VIEW, Permission.BOOKINGS_MANAGE})
"""ADR 0039: permissions that show citizens' personal data; never held by the auditor."""

MAINTENANCE_WRITABLE = frozenset(
    {Permission.INTEGRATIONS_MANAGE, Permission.SYSTEM_UPDATE, Permission.SYSTEM_MAINTENANCE}
)
"""ADR 0038: the permissions that may still change something during maintenance, and then
for the superuser only (``decide_in_maintenance``)."""

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
        Permission.AUDIT_EXPORT,
        Permission.HOLIDAYS_ASSIGN,
        Permission.GROUP_CREATE,
        Permission.GROUP_VIEW,
        Permission.GROUP_EDIT,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.REMINDERS_MANAGE,
        Permission.BOOKINGS_VIEW,
        # ADR 0044: change existing bookings from the agenda.
        Permission.BOOKINGS_MANAGE,
        # ADR 0024: close one group or every group of the organisation at once.
        Permission.CLOSURE_MANAGE,
        Permission.ORGANISATION_CLOSURE,
        # ADR 0039: whether the organisation's personal data is removed on time.
        Permission.RETENTION_VIEW,
        # ADR 0046: the staff of the own organisations.
        Permission.STAFF_VIEW,
        # ADR 0035: the host websites allowed to embed the organisation's groups.
        Permission.EMBEDDING_MANAGE,
    },
    # HANDOVER §6: own groups: rules, overrides, mail texts, view bookings.
    Role.GROUP_ADMIN: _EVERY_ADMIN
    | {
        # ADR 0031: the audit and system events of the own groups, for troubleshooting.
        Permission.AUDIT_VIEW,
        Permission.GROUP_VIEW,
        Permission.SCHEDULE_MANAGE,
        Permission.TEXTS_MANAGE,
        Permission.BOOKINGS_VIEW,
        # ADR 0044: the team's own planner changes its bookings.
        Permission.BOOKINGS_MANAGE,
        # ADR 0024: the team itself can close in an emergency and handle its bookings.
        Permission.CLOSURE_MANAGE,
        # ADR 0046: who else administers the own groups, read-only.
        Permission.STAFF_VIEW,
    },
    # ADR 0039: compliance and privacy (FG, CISO, auditors): reading only, no personal data.
    Role.AUDITOR: _EVERY_ADMIN
    | {
        Permission.AUDIT_VIEW,
        Permission.AUDIT_EXPORT,
        Permission.GROUP_VIEW,
        Permission.RETENTION_VIEW,
        # ADR 0046: who has access, for an access review.
        Permission.STAFF_VIEW,
    },
}

REACH: dict[Role, Reach] = {
    Role.SUPERUSER: Reach.EVERYTHING,
    Role.ORG_ADMIN: Reach.ORGANISATIONS,
    Role.GROUP_ADMIN: Reach.GROUPS,
    Role.AUDITOR: Reach.ORGANISATIONS,
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
    MAINTENANCE = "maintenance"


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


def reaches(principal: Principal, permission: Permission | None = None) -> set[Reach]:
    """Return the reach of the principal's roles (only those granting ``permission``, if given)."""
    roles = principal.roles if permission is None else _granting_roles(principal, permission)
    return {REACH[role] for role in roles}


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
    reach = {REACH[role] for role in roles}
    if Reach.EVERYTHING in reach:
        return Decision(True, Reason.SUPERUSER)
    if target.level is Level.GLOBAL:
        return Decision(True, Reason.ROLE)
    if Reach.ORGANISATIONS in reach and target.organisation in principal.organisations:
        return Decision(True, Reason.ORGANISATION)
    if target.level is Level.GROUP and Reach.GROUPS in reach and target.group in principal.groups:
        return Decision(True, Reason.GROUP)
    return Decision(False, Reason.OUT_OF_SCOPE)


def decide_in_maintenance(
    principal: Principal, permission: Permission, *, maintenance_safe: bool = False
) -> Decision:
    """Decide whether a change that ``decide`` allowed may go ahead during maintenance (ADR 0038).

    Only permissions in ``MAINTENANCE_WRITABLE``, or a route that declares itself
    ``maintenance_safe`` (it writes nothing but its own report), and only for a role that
    reaches everything. The superuser is not an exception beyond that.
    """
    writable = permission in MAINTENANCE_WRITABLE or maintenance_safe
    if writable and Reach.EVERYTHING in reaches(principal, permission):
        return Decision(True, Reason.SUPERUSER)
    return Decision(False, Reason.MAINTENANCE)


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
    reach = reaches(principal, permission)
    if Reach.EVERYTHING in reach:
        return Scope(everything=True)
    organisations = principal.organisations if Reach.ORGANISATIONS in reach else frozenset()
    groups = principal.groups if Reach.GROUPS in reach and level is Level.GROUP else frozenset()
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
