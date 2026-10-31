"""The staff directory: who uses the admin portal, with what roles and where (ADR 0046).

The identity provider decides who someone is and what they may do (ADR 0013). The portal
records what it saw at each login, just in time: one ``Membership`` per grant (a role on an
organisation or a group, ADR 0049 point 12), and the entitlement values it could not use
(``RefusedEntitlement``, ADR 0049 point 8). That record feeds the staff overview and is never
a source of rights; the session still carries the claims.

    login ──► memberships_of(principal) ──► StaffMember ──► changes(old, new) ──► audit events

Who may see whom follows ``staff.view`` (a group-level permission, like ``audit.view``): an
organisation admin or auditor sees staff with a membership in their organisations, a group
admin staff with a membership in their groups. Superusers are seen only by the superuser.
Memberships outside the viewer's scope are left out of what they see (``within``), so the
list never tells an organisation admin where else a colleague works.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import Role, Scope

CREATED_EVENT = "staff.created"
"""Audit event of a person's first login (ADR 0046)."""

CHANGED_EVENT = "staff.membership_changed"
"""Audit event of a login whose roles, organisations or groups differ from the last one."""

ROLE_ADDED = "role.add"
ROLE_REMOVED = "role.remove"
GRANT_ADDED = "grant.add"
GRANT_REMOVED = "grant.remove"


@dataclass(frozen=True)
class Membership:
    """One grant as the directory records it: a role on an organisation or on a group.

    Attributes:
        role: The role's key (``org_admin``, ``custom.3``).
        organisation: Slug of the organisation.
        group: Slug of the group; ``None`` for a grant on the organisation itself.
    """

    role: str
    organisation: str
    group: str | None = None

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """Organisation first, its own grants before those of its groups, then the role."""
        return (self.organisation, self.group or "", self.role)

    @property
    def key(self) -> str:
        """The grant's audit key: ``org_admin:demo``, ``group_admin:demo/team``."""
        place = f"{self.organisation}/{self.group}" if self.group else self.organisation
        return f"{self.role}:{place}"


@dataclass(frozen=True)
class RefusedEntitlement:
    """An entitlement value received at login that gave no right (ADR 0049 point 8).

    Attributes:
        value: The value (prefix, role key, IdP values; never personal data).
        reason: A ``RefusalReason`` code.
        organisation: Slug of the organisation it names, when known; who may see it.
        received_at: When it was last received (UTC).
    """

    value: str
    reason: str
    organisation: str | None
    received_at: datetime


@dataclass(frozen=True)
class GrantHolders:
    """How many people the directory last saw holding one grant (ADR 0049 point 17).

    Attributes:
        membership: The grant.
        holders: How many staff records carry it.
        last_login_at: The latest login among them (UTC).
    """

    membership: Membership
    holders: int
    last_login_at: datetime


@dataclass(frozen=True)
class StaffMember:
    """A person who logged in to the admin portal, as last seen.

    Attributes:
        subject: Subject identifier from the identity provider.
        display_name: Name from the identity provider; never logged.
        roles: Built-in roles from the last login (for the superuser rule and the filter).
        memberships: One per grant of the last login.
        refused: Entitlement values of the last login that were refused.
        first_seen_at: First login (UTC).
        last_login_at: Latest login (UTC).
        last_seen_at: Latest request, written at most once per interval (UTC).
        last_logout_at: Latest explicit logout (UTC); ``None`` if never.
        recorded: ``False`` for a record carried over from before ADR 0046, whose roles and
            memberships are not known yet; its next login writes no change event.
        id: Database id, used in links instead of the subject.
    """

    subject: str
    display_name: str = field(repr=False)
    roles: frozenset[Role]
    memberships: frozenset[Membership]
    first_seen_at: datetime
    last_login_at: datetime
    last_seen_at: datetime
    last_logout_at: datetime | None = None
    recorded: bool = True
    id: int | None = None
    refused: tuple[RefusedEntitlement, ...] = ()

    @property
    def organisations(self) -> list[str]:
        """Slugs of the organisations of the memberships, sorted."""
        return sorted({m.organisation for m in self.memberships})

    def groups_of(self, organisation: str) -> list[str]:
        """Slugs of the groups of the memberships in ``organisation``, sorted."""
        return sorted(
            m.group for m in self.memberships if m.organisation == organisation and m.group
        )

    def is_dormant(self, now: datetime, after: timedelta) -> bool:
        """Whether the person has not logged in for longer than ``after``."""
        return now - self.last_login_at > after

    def is_signed_in(self, now: datetime, lifetime: timedelta) -> bool:
        """Whether the person's session can still be open at ``now``.

        Open means: no logout since the last login, and seen within ``lifetime`` (the absolute
        session lifetime, ``ADMIN_SESSION_HOURS``). A closed browser is not a logout, so this
        is an upper bound.
        """
        logged_out = self.last_logout_at is not None and self.last_logout_at >= self.last_login_at
        return not logged_out and now - self.last_seen_at <= lifetime

    def within(self, scope: Scope) -> "StaffMember":
        """Return the member with only the memberships and refusals ``scope`` covers.

        A refused value shows to a viewer whose scope holds the organisation it names; one
        naming no known organisation shows to the superuser only (ADR 0049, A14).
        """
        if scope.everything:
            return self
        kept = frozenset(m for m in self.memberships if _covers(scope, m))
        refused = tuple(
            r
            for r in self.refused
            if r.organisation is not None and r.organisation in scope.organisations
        )
        return replace(self, memberships=kept, refused=refused)


@dataclass(frozen=True)
class StaffFilter:
    """What a viewer narrowed the staff list to; always applied inside their scope.

    Attributes:
        organisation: Only people with a membership in this organisation (slug).
        group: Only people with a membership in this group (slug).
        role: Only people holding this role.
        dormant_before: Only people whose last login is before this moment.
        id: Only the person with this id (the detail page).
        unrecognised: Only people whose last login carried a refused entitlement value.
    """

    organisation: str | None = None
    group: str | None = None
    role: Role | None = None
    dormant_before: datetime | None = None
    id: int | None = None
    unrecognised: bool = False

    def matches(self, member: StaffMember) -> bool:
        """Return whether ``member`` passes (the SQL adapter applies the same rules)."""
        return (
            (self.organisation is None or self.organisation in member.organisations)
            and (self.group is None or any(m.group == self.group for m in member.memberships))
            and (self.role is None or self.role in member.roles)
            and (self.dormant_before is None or member.last_login_at < self.dormant_before)
            and (self.id is None or member.id == self.id)
            and (not self.unrecognised or bool(member.refused))
        )


def _covers(scope: Scope, membership: Membership) -> bool:
    """Whether ``scope`` covers one membership (an organisation scope covers its groups)."""
    if membership.group is None:
        return membership.organisation in scope.organisations
    return scope.contains(membership.organisation, membership.group)


def visible(member: StaffMember, scope: Scope) -> bool:
    """Whether a viewer with ``scope`` may see ``member`` (the SQL adapter does the same).

    Superusers are visible to the superuser only; everyone else when at least one of their
    memberships falls inside the scope.
    """
    if scope.everything:
        return True
    if Role.SUPERUSER in member.roles:
        return False
    return any(_covers(scope, m) for m in member.memberships)


def _role_changes(old: StaffMember, new: StaffMember) -> list[str]:
    """Detail keys of the roles added and removed, sorted."""
    return [f"{ROLE_ADDED}:{r}" for r in sorted(new.roles - old.roles)] + [
        f"{ROLE_REMOVED}:{r}" for r in sorted(old.roles - new.roles)
    ]


def _membership_changes(added: set[Membership], removed: set[Membership]) -> list[str]:
    """Detail keys of the grants added and removed, sorted (``grant.add:org_admin:demo``)."""
    keys: list[str] = []
    for members, key in ((added, GRANT_ADDED), (removed, GRANT_REMOVED)):
        keys += [f"{key}:{m.key}" for m in sorted(members, key=lambda m: m.sort_key)]
    return keys


def change_events(old: StaffMember | None, new: StaffMember) -> list[AuditEvent]:
    """Return the audit events of a login that created ``new`` or changed ``old`` into it.

    One event per membership of either version, so the admins of each organisation and group
    concerned see it in their own log (ADR 0031). Each event's detail holds the role changes
    and only the membership changes of its own organisation, never those elsewhere. Without
    memberships the event is global (only the superuser sees it). Detail keys are codes and
    slugs (``role.add:org_admin``, ``grant.remove:group_admin:demo/team``), never names.
    """
    if old is not None and not old.recorded:
        return []
    if old is None:
        action, before = CREATED_EVENT, frozenset[Membership]()
        roles = [f"{ROLE_ADDED}:{r}" for r in sorted(new.roles)]
    else:
        action, before = CHANGED_EVENT, old.memberships
        roles = _role_changes(old, new)
    added, removed = set(new.memberships - before), set(before - new.memberships)
    if old is not None and not roles and not added and not removed:
        return []
    contexts = sorted(before | new.memberships, key=lambda m: m.sort_key)

    def event(organisation: str | None, group: str | None) -> AuditEvent:
        local_added = {m for m in added if organisation in (None, m.organisation)}
        local_removed = {m for m in removed if organisation in (None, m.organisation)}
        detail = ",".join(roles + _membership_changes(local_added, local_removed))
        return AuditEvent(
            new.last_login_at, new.subject, action, Outcome.SUCCESS, "", organisation, group, detail
        )

    if not contexts:
        return [event(None, None)]
    return [event(m.organisation, m.group) for m in contexts]
