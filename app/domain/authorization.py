"""The authorisation matrix: the single source of rights for every layer (ADR 0014).

Roles come from the identity provider (ADR 0013). Each permission applies at one level:

    GLOBAL        something about the admin portal itself (dashboard, own rights, the
                  holiday calendars every organisation can reuse, updates of the installation)
    ORGANISATION  something of one organisation (new groups, integrations, branding)
    GROUP         something of one group (settings, opening hours, texts, bookings, the
                  audit log of its events)

A principal holds *grants* (ADR 0049): each one pairs a role with the one target it was given
for, so a role from one organisation never reaches another. A role grants a permission within
its reach (``REACH``):

    superuser    everything: every organisation and group (a scope, not a bypass); no target
    org_admin    organisations: the organisation of the grant, and its groups
    auditor      organisations, read-only and without personal data (ADR 0039)
    group_admin  groups: the one group of the grant, in its organisation

``decide`` looks at one grant at a time: a permission is allowed on a target when a single
grant both holds it (through its role) and covers the target. There is no step that combines
a role of one grant with the scope of another. ``scope_for`` is the union of the covering
grants. How grants are made from the identity provider's claims is ``app.domain.entitlements``.

Some permissions are locked to the superuser (``LOCKED_PERMISSIONS``); some show citizens'
personal data (``PII_PERMISSIONS``); some depend on others (``REQUIRES``). The matrix page and
the documentation mark all three.

Custom roles (ADR 0039) are data: the superuser copies a template (``TEMPLATES``) and switches
delegable permissions on or off. ``validate_role`` keeps every definition inside the bounds;
the database adapter stores it; a ``RoleCatalogue`` attaches the custom roles a principal's
grants name (by key), once per request. ``decide`` and ``scope_for`` read each role's reach,
never a role's name, and clip a custom role to its bounds again::

    grants (role key, organisation, group) ──RoleCatalogue.holding──► custom roles attached
                                                             └─ granted = permissions ∩ delegable

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
from dataclasses import dataclass, field, replace
from enum import StrEnum


class Role(StrEnum):
    """Admin roles (HANDOVER B8)."""

    SUPERUSER = "superuser"
    ORG_ADMIN = "org_admin"
    GROUP_ADMIN = "group_admin"
    AUDITOR = "auditor"
    AGENT = "agent"
    """ADR 0043: works the own queue of its groups (NL: medewerker)."""


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
    ROLES_MANAGE = "roles.manage"
    STAFF_VIEW = "staff.view"
    EMBEDDING_MANAGE = "embedding.manage"
    QUEUE_WORK = "queue.work"
    SETTINGS_PLATFORM_VIEW = "settings.platform.view"
    SETTINGS_PLATFORM_MANAGE = "settings.platform.manage"
    SETTINGS_LOGIN_MANAGE = "settings.login.manage"
    SETTINGS_ORGANISATION_MANAGE = "settings.organisation.manage"
    TEXTS_ORGANISATION_MANAGE = "texts.organisation.manage"
    IDP_MAPPING_VIEW = "idp_mapping.view"
    IDP_MAPPING_MANAGE = "idp_mapping.manage"


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
    # ADR 0039: create, change and delete custom roles.
    Permission.ROLES_MANAGE: Level.GLOBAL,
    # ADR 0046: who uses the portal; group level so a group admin sees their own team.
    Permission.STAFF_VIEW: Level.GROUP,
    # ADR 0035: on which websites the organisation's groups may be embedded.
    Permission.EMBEDDING_MANAGE: Level.ORGANISATION,
    # ADR 0043: see and claim the bookings in a group's own queue.
    Permission.QUEUE_WORK: Level.GROUP,
    # ADR 0042 §5, §11: System status and Advanced settings, read-only.
    Permission.SETTINGS_PLATFORM_VIEW: Level.GLOBAL,
    # ADR 0042 §5: change a platform setting (class C); the login mapping has its own.
    Permission.SETTINGS_PLATFORM_MANAGE: Level.GLOBAL,
    # ADR 0042 §8: the OIDC claim and role mapping, which can lock everyone out.
    Permission.SETTINGS_LOGIN_MANAGE: Level.GLOBAL,
    # ADR 0042 §5: the settings of one organisation (class D).
    Permission.SETTINGS_ORGANISATION_MANAGE: Level.ORGANISATION,
    # ADR 0004, 0009: the organisation's own wording of citizen pages and messages.
    Permission.TEXTS_ORGANISATION_MANAGE: Level.ORGANISATION,
    # ADR 0049 point 17: the entitlement values the IdP must send, per organisation.
    Permission.IDP_MAPPING_VIEW: Level.ORGANISATION,
    # ADR 0049 point 4: the IdP value of a group; not for a group admin.
    Permission.IDP_MAPPING_MANAGE: Level.ORGANISATION,
}

LOCKED_PERMISSIONS = frozenset(
    {
        Permission.ORGANISATION_MANAGE,
        Permission.HOLIDAYS_MANAGE,
        Permission.INTEGRATIONS_MANAGE,
        Permission.API_CLIENTS_MANAGE,
        Permission.SYSTEM_UPDATE,
        Permission.ROLES_MANAGE,
        Permission.SYSTEM_MAINTENANCE,
        Permission.DIAGNOSTICS_MANAGE,
        Permission.SETTINGS_PLATFORM_MANAGE,
        Permission.SETTINGS_LOGIN_MANAGE,
    }
)
"""ADR 0039: permissions that affect more than one organisation or touch secrets; superuser only."""

PII_PERMISSIONS = frozenset(
    {Permission.BOOKINGS_VIEW, Permission.BOOKINGS_MANAGE, Permission.QUEUE_WORK}
)
"""ADR 0039: permissions that show citizens' personal data; never held by the auditor."""

MAINTENANCE_WRITABLE = frozenset(
    {Permission.INTEGRATIONS_MANAGE, Permission.SYSTEM_UPDATE, Permission.SYSTEM_MAINTENANCE}
)
"""ADR 0038: the permissions that may still change something during maintenance, and then
for the superuser only (``decide_in_maintenance``)."""

READ_PERMISSIONS = frozenset(
    {
        Permission.DASHBOARD_VIEW,
        Permission.RIGHTS_VIEW,
        Permission.MATRIX_VIEW,
        Permission.AUDIT_VIEW,
        Permission.AUDIT_EXPORT,
        Permission.GROUP_VIEW,
        Permission.BOOKINGS_VIEW,
        Permission.RETENTION_VIEW,
        Permission.STAFF_VIEW,
        Permission.SETTINGS_PLATFORM_VIEW,
        Permission.IDP_MAPPING_VIEW,
    }
)
"""ADR 0039: permissions that only show; everything else changes something."""

_NEEDS_GROUP_VIEW = frozenset({Permission.GROUP_VIEW})

REQUIRES: dict[Permission, frozenset[Permission]] = {
    Permission.GROUP_EDIT: _NEEDS_GROUP_VIEW,
    Permission.SCHEDULE_MANAGE: _NEEDS_GROUP_VIEW,
    Permission.TEXTS_MANAGE: _NEEDS_GROUP_VIEW,
    Permission.REMINDERS_MANAGE: _NEEDS_GROUP_VIEW,
    Permission.CLOSURE_MANAGE: _NEEDS_GROUP_VIEW,
    # ADR 0044: a planner changes bookings they can see in the group's agenda.
    Permission.BOOKINGS_MANAGE: frozenset({Permission.BOOKINGS_VIEW, Permission.GROUP_VIEW}),
    Permission.ORGANISATION_CLOSURE: frozenset({Permission.CLOSURE_MANAGE}),
    # ADR 0049: changing a group's IdP value presumes seeing the mapping.
    Permission.IDP_MAPPING_MANAGE: frozenset({Permission.IDP_MAPPING_VIEW}),
}
"""ADR 0039: permissions a permission depends on; a custom role must hold them too."""

EVERY_ADMIN = frozenset({Permission.DASHBOARD_VIEW, Permission.RIGHTS_VIEW, Permission.MATRIX_VIEW})
"""ADR 0039: what every admin holds, so every role (custom ones too) must hold it."""

GLOBAL_FOR_ANY_REACH = EVERY_ADMIN | {Permission.SETTINGS_PLATFORM_VIEW}
"""Global permissions a role without the superuser's reach may hold: the basics, and the
read-only System status and Advanced settings (ADR 0042 §5, no secret values shown)."""

MATRIX: dict[Role, frozenset[Permission]] = {
    # HANDOVER §6: everything, including organisations, IdP, SMTP and WxCC credentials.
    Role.SUPERUSER: frozenset(Permission),
    # HANDOVER §6: own organisation: groups, rules, overrides, texts, bookings of its groups;
    # ADR 0025: which of the superuser's holiday calendars its groups follow.
    Role.ORG_ADMIN: EVERY_ADMIN
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
        # ADR 0043: can step in as an agent in the own queue.
        Permission.QUEUE_WORK,
        # ADR 0042 §5: the settings of the own organisation (class D).
        Permission.SETTINGS_ORGANISATION_MANAGE,
        # ADR 0004: the organisation's own wording of citizen pages and messages.
        Permission.TEXTS_ORGANISATION_MANAGE,
        # ADR 0049: the entitlement values of the own organisations, and the groups' IdP values.
        Permission.IDP_MAPPING_VIEW,
        Permission.IDP_MAPPING_MANAGE,
    },
    # HANDOVER §6: own groups: rules, overrides, mail texts, view bookings.
    Role.GROUP_ADMIN: EVERY_ADMIN
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
        # ADR 0043: can step in as an agent in the own queue.
        Permission.QUEUE_WORK,
    },
    # ADR 0039: compliance and privacy (FG, CISO, auditors): reading only, no personal data.
    Role.AUDITOR: EVERY_ADMIN
    | {
        Permission.AUDIT_VIEW,
        Permission.AUDIT_EXPORT,
        Permission.GROUP_VIEW,
        Permission.RETENTION_VIEW,
        # ADR 0046: who has access, for an access review.
        Permission.STAFF_VIEW,
        # ADR 0042 §5: how the installation is configured, without secret values.
        Permission.SETTINGS_PLATFORM_VIEW,
        # ADR 0049 point 17: the mapping is an input for the access review.
        Permission.IDP_MAPPING_VIEW,
    },
    # ADR 0043: picks up bookings in the own queue of its groups; sees first names only.
    Role.AGENT: EVERY_ADMIN | {Permission.GROUP_VIEW, Permission.QUEUE_WORK},
}

REACH: dict[Role, Reach] = {
    Role.SUPERUSER: Reach.EVERYTHING,
    Role.ORG_ADMIN: Reach.ORGANISATIONS,
    Role.GROUP_ADMIN: Reach.GROUPS,
    Role.AUDITOR: Reach.ORGANISATIONS,
    Role.AGENT: Reach.GROUPS,
}

TEMPLATES: tuple[Role, ...] = (Role.ORG_ADMIN, Role.GROUP_ADMIN, Role.AUDITOR)
"""ADR 0039: the built-in roles a custom role may copy; never the superuser."""

HELD_BACK = frozenset({Permission.BRANDING_MANAGE})
"""Not locked, but kept out of custom roles: ADR 0039 B3 leaves branding with the superuser
through the built-in roles until O5 (branding per organisation) is settled."""

READ_ONLY_TEMPLATES = frozenset({Role.AUDITOR})
"""Templates whose copies stay read-only and without personal data (ADR 0039, item 10)."""


def _within_reach(reach: Reach, permission: Permission) -> bool:
    """Return whether a role of ``reach`` can hold ``permission`` at all."""
    level = LEVELS[permission]
    if reach is Reach.EVERYTHING:
        return True
    if level is Level.GLOBAL:
        return permission in GLOBAL_FOR_ANY_REACH  # anything else global changes the installation
    return level is Level.GROUP or reach is Reach.ORGANISATIONS


def delegable(template: Role) -> frozenset[Permission]:
    """Return the permissions a custom role copied from ``template`` may hold.

    Not locked or held back, within the template's reach and, for a read-only template, only reading
    permissions without personal data. Empty for a role that is not a template.
    """
    if template not in TEMPLATES:
        return frozenset()
    reach = REACH[template]
    allowed = {
        p for p in Permission if p not in LOCKED_PERMISSIONS | HELD_BACK and _within_reach(reach, p)
    }
    if template in READ_ONLY_TEMPLATES:
        allowed &= READ_PERMISSIONS - PII_PERMISSIONS
    return frozenset(allowed)


class RoleRule(StrEnum):
    """Why ``validate_role`` refuses a custom role definition (a code, never text)."""

    TEMPLATE = "template"
    LOCKED = "locked"
    ABOVE_REACH = "above_reach"
    PII = "pii"
    WRITE = "write"
    REQUIREMENT = "requirement"
    BASICS = "basics"


@dataclass(frozen=True)
class Violation:
    """One broken rule, with the permission concerned (``None`` for the template)."""

    rule: RoleRule
    permission: Permission | None


def validate_role(template: Role, permissions: Iterable[Permission]) -> list[Violation]:
    """Return every rule a custom role definition breaks; empty means it is valid.

    The order is stable (rule, then permission), so the editor shows the same list each time.
    """
    if template not in TEMPLATES:
        return [Violation(RoleRule.TEMPLATE, None)]
    held = frozenset(permissions)
    reach = REACH[template]
    found: list[Violation] = []
    for permission in sorted(held):
        if permission in LOCKED_PERMISSIONS | HELD_BACK:
            found.append(Violation(RoleRule.LOCKED, permission))
        elif not _within_reach(reach, permission):
            found.append(Violation(RoleRule.ABOVE_REACH, permission))
        elif template in READ_ONLY_TEMPLATES and permission in PII_PERMISSIONS:
            found.append(Violation(RoleRule.PII, permission))
        elif template in READ_ONLY_TEMPLATES and permission not in READ_PERMISSIONS:
            found.append(Violation(RoleRule.WRITE, permission))
    found += [
        Violation(RoleRule.REQUIREMENT, permission)
        for permission in sorted(held)
        if not REQUIRES.get(permission, frozenset()) <= held
    ]
    found += [Violation(RoleRule.BASICS, p) for p in sorted(EVERY_ADMIN - held)]
    order = list(RoleRule)
    return sorted(found, key=lambda v: (order.index(v.rule), v.permission or ""))


@dataclass(frozen=True)
class CustomRole:
    """A role the superuser defined (ADR 0039), as stored.

    Attributes:
        id: Database id; ``key`` is derived from it.
        template: The built-in role it was copied from; fixes its reach.
        permissions: The permissions switched on (validated when saved, clipped when used).
        claim_value: The role claim value of the identity provider that grants it.
        name_nl: Name in Dutch.
        name_en: Name in English.
        version: Starts at 1 and goes up with every change.
    """

    id: int
    template: Role
    permissions: frozenset[Permission]
    claim_value: str
    name_nl: str
    name_en: str
    version: int = 1

    @property
    def key(self) -> str:
        """Stable reference in audit events and logs, e.g. ``custom.3``."""
        return f"custom.{self.id}"

    @property
    def reach(self) -> Reach:
        """The reach of the template: ``organisations`` or ``groups``, never everything."""
        return REACH[self.template] if self.template in TEMPLATES else Reach.GROUPS

    @property
    def granted(self) -> frozenset[Permission]:
        """What ``decide`` honours: the definition clipped to its bounds once more."""
        return self.permissions & delegable(self.template)

    def name(self, locale: str) -> str:
        """Return the name in ``locale``; English for any locale other than Dutch."""
        return self.name_nl if locale == "nl" else self.name_en


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
class Grant:
    """One entitlement: a role on exactly one target (ADR 0049).

    Attributes:
        role: The role's key: a built-in ``Role`` value or ``custom.<id>``.
        organisation: Organisation slug; ``None`` only for a role that reaches everything.
        group: Group slug, for a role that reaches groups; ``None`` otherwise.
    """

    role: str
    organisation: str | None = None
    group: str | None = None

    @property
    def key(self) -> str:
        """A stable, readable form for audit details: ``org_admin:demo``, ``superuser``."""
        if self.organisation is None:
            return self.role
        place = f"{self.organisation}/{self.group}" if self.group else self.organisation
        return f"{self.role}:{place}"


@dataclass(frozen=True)
class Principal:
    """A logged-in admin: who they are and what they were granted.

    Attributes:
        subject: Stable identifier from the identity provider; used in logs and audit.
        display_name: For the page header only; never logged.
        grants: One per role and target (ADR 0049); resolved per request from the session's
            claims, so a renamed or deleted organisation or group takes effect at once.
        custom_roles: The custom roles the grants name, attached per request by a
            ``RoleCatalogue`` (never stored in the session).
    """

    subject: str
    display_name: str = field(repr=False)
    grants: frozenset[Grant] = frozenset()
    custom_roles: frozenset[CustomRole] = frozenset()

    @property
    def roles(self) -> frozenset[Role]:
        """The built-in roles among the grants (for labels and default layouts)."""
        names = {r.value for r in Role}
        return frozenset(Role(g.role) for g in self.grants if g.role in names)

    def grants_of(self, role: str) -> frozenset[Grant]:
        """Return the grants of one role key."""
        return frozenset(g for g in self.grants if g.role == role)


@dataclass(frozen=True)
class RoleCatalogue:
    """The custom roles of the installation (ADR 0039, B2: one catalogue for all)."""

    custom: tuple[CustomRole, ...] = ()

    def holding(self, principal: Principal) -> Principal:
        """Return ``principal`` with exactly the custom roles its grants name by key."""
        keys = {g.role for g in principal.grants}
        return replace(principal, custom_roles=frozenset(r for r in self.custom if r.key in keys))

    def by_claim_value(self, values: Iterable[str]) -> frozenset[CustomRole]:
        """Return the custom roles the legacy role claim ``values`` grant (ADR 0039 point 6)."""
        wanted = set(values)
        return frozenset(r for r in self.custom if r.claim_value in wanted)

    def reach_by_key(self) -> dict[str, Reach]:
        """Return the reach of every role key: built-in roles, then the custom ones."""
        found: dict[str, Reach] = {role.value: REACH[role] for role in Role}
        found.update({r.key: r.reach for r in self.custom})
        return found


def holds_a_role(principal: Principal) -> bool:
    """Return whether the principal holds a grant of a known role (built-in or attached)."""
    return bool(_grants(principal))


def role_keys(principal: Principal) -> list[str]:
    """Return the principal's distinct role keys, sorted (built-in values and ``custom.<id>``)."""
    return sorted({g.role for g in principal.grants})


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
    """Which organisations and groups a listing may show; applied in SQL by the adapter.

    Attributes:
        everything: The superuser's scope.
        organisations: Organisation slugs (with every group of each).
        groups: ``(organisation slug, group slug)`` pairs (ADR 0049 point 11): a group is
            only in scope together with its organisation, so a slug reused elsewhere never
            matches.
    """

    everything: bool = False
    organisations: frozenset[str] = frozenset()
    groups: frozenset[tuple[str, str]] = frozenset()

    @property
    def is_empty(self) -> bool:
        """Whether the scope matches nothing."""
        return not (self.everything or self.organisations or self.groups)

    @property
    def group_slugs(self) -> frozenset[str]:
        """The group slugs of the pairs, for labels."""
        return frozenset(group for _, group in self.groups)

    def contains(self, organisation: str | None, group: str | None) -> bool:
        """Return whether a group (with its organisation) falls inside the scope."""
        return (
            self.everything
            or (organisation is not None and organisation in self.organisations)
            or (
                organisation is not None
                and group is not None
                and (organisation, group) in self.groups
            )
        )


_BUILT_IN = {role.value: role for role in Role}


def _role_of(principal: Principal, key: str) -> tuple[frozenset[Permission], Reach] | None:
    """Return the permissions and reach of role ``key``, or ``None`` for an unknown key."""
    role = _BUILT_IN.get(key)
    if role is not None:
        return MATRIX[role], REACH[role]
    for custom in principal.custom_roles:
        if custom.key == key:
            return custom.granted, custom.reach
    return None


def _grants(principal: Principal) -> list[tuple[frozenset[Permission], Reach, Grant]]:
    """Return every grant with its role's permissions and reach; unknown roles grant nothing."""
    found = []
    for grant in sorted(principal.grants, key=lambda g: g.key):
        role = _role_of(principal, grant.role)
        if role is not None:
            found.append((role[0], role[1], grant))
    return found


def reaches(principal: Principal, permission: Permission | None = None) -> set[Reach]:
    """Return the reach of the principal's roles (only those granting ``permission``, if given)."""
    return {
        reach
        for permissions, reach, _ in _grants(principal)
        if permission is None or permission in permissions
    }


def _covers(reach: Reach, grant: Grant, target: Target) -> Reason | None:
    """Return why one grant covers ``target``, or ``None`` when it does not (ADR 0049 point 10)."""
    if reach is Reach.EVERYTHING:
        return Reason.SUPERUSER
    if target.level is Level.GLOBAL:
        return Reason.ROLE
    if reach is Reach.ORGANISATIONS:
        return Reason.ORGANISATION if grant.organisation == target.organisation else None
    if target.level is Level.GROUP and (grant.organisation, grant.group) == (
        target.organisation,
        target.group,
    ):
        return Reason.GROUP
    return None


def decide(principal: Principal, permission: Permission, target: Target) -> Decision:
    """Decide whether ``principal`` may use ``permission`` on ``target``.

    Raises:
        ValueError: If the target's level does not match the permission (a programming error).
    """
    if target.level is not LEVELS[permission]:
        raise ValueError(f"{permission} needs a {LEVELS[permission]} level target")
    if not holds_a_role(principal):
        return Decision(False, Reason.NO_ROLE)
    holding = [
        (reach, grant)
        for permissions, reach, grant in _grants(principal)
        if permission in permissions
    ]
    if not holding:
        return Decision(False, Reason.NOT_PERMITTED)
    reasons = {_covers(reach, grant, target) for reach, grant in holding} - {None}
    for reason in (Reason.SUPERUSER, Reason.ROLE, Reason.ORGANISATION, Reason.GROUP):
        if reason in reasons:
            return Decision(True, reason)
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
    holding = [
        (reach, grant)
        for permissions, reach, grant in _grants(principal)
        if permission in permissions
    ]
    if any(reach is Reach.EVERYTHING for reach, _ in holding):
        return Scope(everything=True)
    organisations = frozenset(
        g.organisation for reach, g in holding if reach is Reach.ORGANISATIONS and g.organisation
    )
    groups = frozenset(
        (g.organisation, g.group)
        for reach, g in holding
        if reach is Reach.GROUPS and level is Level.GROUP and g.organisation and g.group
    )
    return Scope(organisations=organisations, groups=groups)


def permissions_for(principal: Principal) -> list[tuple[Permission, Scope | None]]:
    """Return the principal's permissions with their scope (``None`` for global ones).

    Used by the "My rights" page. Permissions whose scope is empty are left out.
    """
    rights: list[tuple[Permission, Scope | None]] = []
    for permission in Permission:
        if not reaches(principal, permission):
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
