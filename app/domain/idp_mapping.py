"""The identity provider mapping: what the IdP must send, and the IdP value of a group (ADR 0049).

The read-only page *Koppeling identiteitsprovider* (point 17) lists, within the viewer's
scope, the exact entitlement value per role per organisation and group, how many people the
directory last saw holding each one, and the values refused in the last
``idp_mapping_refused_window_days``. Its download is the same list as text, audited.

The group's IdP value (point 4) is changed here under ``idp_mapping.manage`` (organisation
level: an org admin may, a group admin may not), with a fresh login, refused on collision
within the organisation, and audited as ``group.idp_key_changed`` with old and new value. The
organisation's value is changed by the superuser through ``OrganisationService``.

    overview(principal)                      idp_mapping.view
    export(principal)                        idp_mapping.view, audited idp_mapping.exported
    change_group_idp_key(principal, slug, v) idp_mapping.manage on the group's organisation
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEvent, Outcome
from app.domain.authorization import (
    REACH,
    CustomRole,
    Permission,
    Principal,
    Reach,
    Role,
    Scope,
    scope_for,
)
from app.domain.entitlements import ClaimMode, entitlement_value, is_idp_key
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, UnitOfWork
from app.domain.scheduling import GroupSummary
from app.domain.staff import GrantHolders, Membership, RefusedEntitlement

_VIEW = Permission.IDP_MAPPING_VIEW
_MANAGE = Permission.IDP_MAPPING_MANAGE

EXPORTED_EVENT = "idp_mapping.exported"
"""ADR 0049 point 17: the list of entitlement values was downloaded."""

GROUP_IDP_KEY_CHANGED = "group.idp_key_changed"
"""ADR 0049 point 4: an org admin changed the value the IdP names a group by."""


@dataclass(frozen=True)
class MappingSettings:
    """What the page shows about the configuration."""

    mode: ClaimMode
    claim: str
    prefix: str
    window_days: int


@dataclass(frozen=True)
class EntitlementLine:
    """One value the IdP may send: a role on one target, with who holds it."""

    role: str
    value: str
    holders: int = 0
    last_login_at: datetime | None = None


@dataclass(frozen=True)
class MappedGroup:
    """A group with its IdP value and the value per group-reach role."""

    name: str
    slug: str
    idp_key: str
    lines: tuple[EntitlementLine, ...]


@dataclass(frozen=True)
class MappedOrganisation:
    """An organisation with its IdP value, the value per organisation-reach role, its groups."""

    name: str
    slug: str
    idp_key: str
    lines: tuple[EntitlementLine, ...]
    groups: tuple[MappedGroup, ...]


@dataclass(frozen=True)
class RefusedLine:
    """A refused value as the page lists it: once per value, with how many people sent it."""

    value: str
    reason: str
    organisation: str | None
    received_at: datetime
    people: int


@dataclass(frozen=True)
class MappingOverview:
    """Everything the page and the download show."""

    settings: MappingSettings
    superuser_value: str | None
    organisations: tuple[MappedOrganisation, ...]
    refused: tuple[RefusedLine, ...]
    custom_roles: tuple[CustomRole, ...]


def _roles_of(reach: Reach, custom: Sequence[CustomRole]) -> list[str]:
    """Return the role keys of ``reach``: built-in first, then the custom ones."""
    keys = [role.value for role in Role if REACH[role] is reach]
    return keys + [r.key for r in custom if r.reach is reach]


def _lines(
    prefix: str,
    roles: Sequence[str],
    organisation: str,
    group: str | None,
    holders: dict[Membership, GrantHolders],
    organisation_key: str,
    group_key: str | None,
) -> tuple[EntitlementLine, ...]:
    """Return one line per role on the target, with the holders the directory knows."""
    lines = []
    for role in roles:
        found = holders.get(Membership(role, organisation, group))
        lines.append(
            EntitlementLine(
                role,
                entitlement_value(prefix, role, organisation_key, group_key),
                found.holders if found else 0,
                found.last_login_at if found else None,
            )
        )
    return tuple(lines)


def _refused_lines(found: Sequence[RefusedEntitlement]) -> tuple[RefusedLine, ...]:
    """Fold the refusals of every person into one line per value, newest first."""
    by_value: dict[str, RefusedLine] = {}
    for refusal in found:
        line = by_value.get(refusal.value)
        if line is None:
            by_value[refusal.value] = RefusedLine(
                refusal.value, refusal.reason, refusal.organisation, refusal.received_at, 1
            )
        else:
            by_value[refusal.value] = replace(
                line,
                people=line.people + 1,
                received_at=max(line.received_at, refusal.received_at),
            )
    return tuple(sorted(by_value.values(), key=lambda r: (r.reason, r.value)))


def export_text(overview: MappingOverview) -> str:
    """Return the values as a text file for the IdP team: one per line, a comment per target."""
    lines = [
        f"# OpenPlanner entitlements, claim {overview.settings.claim}, "
        f"prefix {overview.settings.prefix}"
    ]
    if overview.superuser_value:
        lines += ["", "# superuser", overview.superuser_value]
    for organisation in overview.organisations:
        lines += [
            "",
            f"# organisation {organisation.slug}",
            *(line.value for line in organisation.lines),
        ]
        for group in organisation.groups:
            lines += [
                "",
                f"# group {organisation.slug}/{group.slug}",
                *(line.value for line in group.lines),
            ]
    return "\n".join(lines) + "\n"


class IdpMappingService:
    """Application service of the identity provider mapping.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises every operation and audits refusals.
        settings: Returns the current mode, claim name, prefix and window.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        settings: Callable[[], MappingSettings],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._settings = settings

    async def overview(self, principal: Principal) -> MappingOverview:
        """Return the mapping within the principal's scope.

        Raises:
            DomainError: ``not_permitted``.
        """
        require(await self._admin.authorize_anywhere(principal, _VIEW))
        settings = self._settings()
        scope = scope_for(principal, _VIEW)
        since = self._clock.now() - timedelta(days=settings.window_days)
        async with self._uow.read() as tx:
            organisations = await tx.list_organisations(scope)
            groups = await tx.list_groups(scope)
            custom = tuple(await tx.get_custom_roles())
            holders = {h.membership: h for h in await tx.list_grant_holders(scope)}
            refused = await tx.list_refused_entitlements(scope, since)
        org_roles = _roles_of(Reach.ORGANISATIONS, custom)
        group_roles = _roles_of(Reach.GROUPS, custom)
        mapped = []
        for organisation in organisations:
            own = [g for g in groups if g.organisation_slug == organisation.slug]
            mapped.append(
                MappedOrganisation(
                    organisation.name,
                    organisation.slug,
                    organisation.idp_key,
                    _lines(
                        settings.prefix,
                        org_roles,
                        organisation.slug,
                        None,
                        holders,
                        organisation.idp_key,
                        None,
                    ),
                    tuple(
                        MappedGroup(
                            g.group.name,
                            g.group.slug,
                            g.group.idp_key,
                            _lines(
                                settings.prefix,
                                group_roles,
                                organisation.slug,
                                g.group.slug,
                                holders,
                                organisation.idp_key,
                                g.group.idp_key,
                            ),
                        )
                        for g in own
                    ),
                )
            )
        superuser = (
            entitlement_value(settings.prefix, Role.SUPERUSER.value) if scope.everything else None
        )
        return MappingOverview(settings, superuser, tuple(mapped), _refused_lines(refused), custom)

    async def export(self, principal: Principal) -> str:
        """Return the overview as text and audit the download (``idp_mapping.exported``).

        The event is written at the organisation's level when the scope holds exactly one
        organisation, else globally.

        Raises:
            DomainError: ``not_permitted``.
        """
        overview = await self.overview(principal)
        scope = scope_for(principal, _VIEW)
        organisation = (
            next(iter(scope.organisations))
            if not scope.everything and len(scope.organisations) == 1
            else None
        )
        async with self._uow.locked() as tx:
            await tx.add_audit_event(
                AuditEvent(
                    self._clock.now(),
                    principal.subject,
                    EXPORTED_EVENT,
                    Outcome.SUCCESS,
                    "",
                    organisation,
                    None,
                    f"organisations:{len(overview.organisations)}",
                )
            )
        return export_text(overview)

    async def group_holders(self, principal: Principal, slug: str) -> int:
        """Return how many people last held a grant on the group (the form's warning).

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        summary = await self._manage(principal, slug)
        async with self._uow.read() as tx:
            holders = await tx.list_grant_holders(
                Scope(organisations=frozenset({summary.organisation_slug}))
            )
        return sum(h.holders for h in holders if h.membership.group == slug)

    async def change_group_idp_key(self, principal: Principal, slug: str, value: str) -> bool:
        """Set the value the IdP names the group by; return whether it changed.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found``, ``invalid_configuration``
                (grammar) or ``idp_key_taken`` (another group of the organisation has it),
                each with field ``idp_key``.
        """
        summary = await self._manage(principal, slug)
        if not is_idp_key(value):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "idp_key")
        async with self._uow.locked() as tx:
            group = await tx.get_group(slug)
            if group is None:
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            if group.idp_key == value:
                return False
            directory = await tx.get_directory()
            for other in directory.groups_by_idp_key(value):
                if other.organisation == summary.organisation_slug and other.slug != slug:
                    raise DomainError(ErrorCode.IDP_KEY_TAKEN, "idp_key")
            await tx.save_group(replace(group, idp_key=value))
            await tx.add_audit_event(
                AuditEvent(
                    self._clock.now(),
                    principal.subject,
                    GROUP_IDP_KEY_CHANGED,
                    Outcome.SUCCESS,
                    "",
                    summary.organisation_slug,
                    slug,
                    f"{group.idp_key}->{value}",
                )
            )
        return True

    async def _manage(self, principal: Principal, slug: str) -> GroupSummary:
        """Authorise ``idp_mapping.manage`` on the group's organisation; return the group.

        Raises:
            DomainError: ``not_permitted`` or ``group_not_found``.
        """
        authorization = await self._admin.authorize(principal, _MANAGE, group_slug=slug)
        require(authorization)
        if authorization.group is None:  # pragma: no cover - authorize looked it up
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return authorization.group
