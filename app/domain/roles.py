"""Custom roles: the superuser's role editor and its change history (ADR 0039 step 3).

The bounds live in ``app/domain/authorization.py`` (``validate_role``); this module adds the
rest of a definition (names, the role claim value) and the use cases around it:

    superuser (roles.manage, fresh login)
        create(draft) ─► validate names, claim value, bounds ─► store version 1 ─► role.created
        update(id, draft, version) ─► same checks, version must match ─► version+1 ─► role.updated
        delete(id, version) ─► holders keep no role from it ─► role.deleted
    every admin (matrix.view)
        catalogue() · history()       role definitions and who changed them are not secret
    every request
        attach(principal) ─► the custom roles its grants name by key (no authorisation)

Every change writes one global ``admin`` audit event in the same transaction: the role key as
reason, the new version and the permission codes added and removed as detail. Names and claim
values are not personal data, but the detail still names only what changed, never a value.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.domain.admin import AdminService, require
from app.domain.audit import AuditEntry, AuditEvent, Outcome
from app.domain.authorization import (
    MATRIX,
    CustomRole,
    Permission,
    Principal,
    Role,
    RoleCatalogue,
    Violation,
    validate_role,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, Transaction, UnitOfWork

ROLE_CREATED = "role.created"
ROLE_UPDATED = "role.updated"
ROLE_DELETED = "role.deleted"
ROLE_EVENTS = (ROLE_CREATED, ROLE_UPDATED, ROLE_DELETED)
"""Audit actions of the role editor; the matrix page lists them as the change history."""

MAX_NAME_LENGTH = 80
CLAIM_VALUE_PATTERN = r"[A-Za-z0-9._:/=@-]{1,128}"
"""A role claim value: what an IdP sends as one group or role name, without spaces or commas
(which split a claim into values, see the OIDC adapter). Also the input's ``pattern``."""

MAX_ROLE_CLAIMS = 20
"""How many unmatched role claim values a session keeps; bounds the session cookie (4 kB)."""

HISTORY_LIMIT = 50
"""Changes shown on the matrix page; the audit log keeps the rest."""

_CLAIM_VALUE = re.compile(CLAIM_VALUE_PATTERN)
_BUILT_IN = frozenset(role.value for role in Role)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MANAGE = Permission.ROLES_MANAGE


def is_claim_value(value: str) -> bool:
    """Return whether ``value`` can be a role claim value (safe to keep in the session)."""
    return bool(_CLAIM_VALUE.fullmatch(value))


class RoleInvalidError(DomainError):
    """The definition breaks a bound of ``validate_role``; ``violations`` says which.

    Args:
        violations: Every broken rule, in the stable order of ``validate_role``.
    """

    def __init__(self, violations: list[Violation]) -> None:
        """Store the violations; the field is the permission fieldset."""
        super().__init__(ErrorCode.ROLE_INVALID, "permissions")
        self.violations = violations


def _name(raw: str, field: str) -> str:
    """Normalise a role name, or refuse it naming ``field``."""
    clean = " ".join(raw.split())
    if not clean or len(clean) > MAX_NAME_LENGTH or _CONTROL_CHARS.search(raw):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, field)
    return clean


@dataclass(frozen=True)
class RoleDraft:
    """What the superuser entered for a custom role. Build it with ``RoleDraft.create``."""

    template: Role
    permissions: frozenset[Permission]
    claim_value: str
    name_nl: str
    name_en: str

    @classmethod
    def create(
        cls,
        *,
        template: str,
        permissions: Iterable[str],
        claim_value: str,
        name_nl: str,
        name_en: str,
    ) -> "RoleDraft":
        """Validate raw form input, then the bounds.

        Raises:
            DomainError: ``invalid_configuration`` naming the field (``template``,
                ``name_nl``, ``name_en``, ``claim_value``, ``permissions``).
            RoleInvalidError: When the permissions break a bound.
        """
        try:
            role = Role(template)
            held = frozenset(Permission(p) for p in permissions)
        except ValueError as exc:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "permissions") from exc
        nl, en = _name(name_nl, "name_nl"), _name(name_en, "name_en")
        claim = claim_value.strip()
        if not is_claim_value(claim):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "claim_value")
        violations = validate_role(role, held)
        if violations:
            raise RoleInvalidError(violations)
        return cls(role, held, claim, nl, en)


MAX_DETAIL_LENGTH = 255
"""What ``audit_events.detail`` holds (a storage limit, not a policy)."""


def change_detail(role: CustomRole, before: CustomRole | None) -> str:
    """Return the audit detail of a change: version and what changed, never a value.

    A new role is recorded relative to its template, so the detail names only what the
    superuser switched on or off: ``version:1,template:group_admin,perm.add:group.edit``.
    A change compares with the previous version: ``version:2,perm.remove:texts.manage,name``.
    When the permission codes would not fit, they are counted instead
    (``perm.add:3``), so a detail is never cut off; the role's page shows them all.
    The role's key goes into the event's ``reason`` (see ``RoleService``).
    """
    old = before.permissions if before else MATRIX[role.template]
    head = [f"version:{role.version}"]
    if before is None:
        head.append(f"template:{role.template}")
    added, removed = sorted(role.permissions - old), sorted(old - role.permissions)
    tail = []
    if before is not None and (before.name_nl, before.name_en) != (role.name_nl, role.name_en):
        tail.append("name")
    if before is not None and before.claim_value != role.claim_value:
        tail.append("claim_value")
    listed = [f"perm.add:{p}" for p in added] + [f"perm.remove:{p}" for p in removed]
    detail = ",".join(head + listed + tail)
    if len(detail) <= MAX_DETAIL_LENGTH:
        return detail
    counted = [f"perm.add:{len(added)}"] * bool(added) + [f"perm.remove:{len(removed)}"] * bool(
        removed
    )
    return ",".join(head + counted + tail)


class RoleService:
    """Application service of the role editor.

    Args:
        uow: Opens database transactions.
        clock: Current time, for versions and audit events.
        admin: Authorises the superuser's changes and audits refusals.
        reserved_claims: The claim values of the built-in roles (``OIDC_ROLE_*``); a custom
            role may not use one of them.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        reserved_claims: Iterable[str],
    ) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._reserved = frozenset(value.casefold() for value in reserved_claims)

    async def attach(self, principal: Principal) -> Principal:
        """Return ``principal`` with the custom roles its grants name now.

        A change takes effect without a new login. No database read when every grant names
        a built-in role.
        """
        if all(g.role in _BUILT_IN for g in principal.grants):
            return principal
        return (await self.catalogue()).holding(principal)

    async def catalogue(self) -> RoleCatalogue:
        """Return every custom role (role definitions are not secret, ADR 0039 item 7)."""
        async with self._uow.read() as tx:
            return RoleCatalogue(tuple(await tx.get_custom_roles()))

    async def history(self, limit: int = HISTORY_LIMIT) -> list[AuditEntry]:
        """Return the newest role changes with their actors' names (for ``matrix.view``)."""
        async with self._uow.read() as tx:
            return await tx.get_events(ROLE_EVENTS, limit)

    async def role(self, principal: Principal, role_id: int) -> CustomRole:
        """Return one custom role for the editor.

        Raises:
            DomainError: ``not_permitted`` or ``role_not_found``.
        """
        await self._authorize(principal)
        async with self._uow.read() as tx:
            return await _role(tx, role_id)

    async def create(self, principal: Principal, draft: RoleDraft) -> CustomRole:
        """Store a new custom role at version 1.

        Raises:
            DomainError: ``not_permitted`` or ``claim_value_taken``.
        """
        await self._authorize(principal)
        async with self._uow.locked() as tx:
            await self._check_claim(tx, draft.claim_value, None)
            role = await tx.add_custom_role(
                CustomRole(
                    0,
                    draft.template,
                    draft.permissions,
                    draft.claim_value,
                    draft.name_nl,
                    draft.name_en,
                )
            )
            await self._audit(tx, principal, ROLE_CREATED, role, change_detail(role, None))
        return role

    async def update(
        self, principal: Principal, role_id: int, draft: RoleDraft, version: int
    ) -> bool:
        """Change names, claim value and permissions; return whether anything changed.

        The template (and with it the reach) never changes.

        Raises:
            DomainError: ``not_permitted``, ``role_not_found``, ``role_changed`` (someone
                saved in between), ``claim_value_taken`` or ``invalid_configuration`` for a
                different template.
        """
        await self._authorize(principal)
        async with self._uow.locked() as tx:
            current = await _current(tx, role_id, version)
            if draft.template is not current.template:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "template")
            await self._check_claim(tx, draft.claim_value, role_id)
            changed = CustomRole(
                current.id,
                current.template,
                draft.permissions,
                draft.claim_value,
                draft.name_nl,
                draft.name_en,
                current.version + 1,
            )
            if replace(changed, version=current.version) == current:
                return False
            await tx.save_custom_role(changed)
            await self._audit(tx, principal, ROLE_UPDATED, changed, change_detail(changed, current))
        return True

    async def delete(self, principal: Principal, role_id: int, version: int) -> CustomRole:
        """Delete a custom role; whoever held it through its claim value holds no role from it.

        Raises:
            DomainError: ``not_permitted``, ``role_not_found`` or ``role_changed``.
        """
        await self._authorize(principal)
        async with self._uow.locked() as tx:
            current = await _current(tx, role_id, version)
            await tx.delete_custom_role(role_id)
            await self._audit(tx, principal, ROLE_DELETED, current, f"version:{current.version}")
        return current

    async def _check_claim(self, tx: Transaction, value: str, role_id: int | None) -> None:
        """Refuse a claim value of a built-in role or of another custom role (any case)."""
        folded = value.casefold()
        if folded in self._reserved or any(
            role.claim_value.casefold() == folded and role.id != role_id
            for role in await tx.get_custom_roles()
        ):
            raise DomainError(ErrorCode.CLAIM_VALUE_TAKEN, "claim_value")

    async def _audit(
        self, tx: Transaction, principal: Principal, action: str, role: CustomRole, detail: str
    ) -> None:
        """Write the change's global audit event (reason: the role key) in its transaction."""
        event = AuditEvent(
            self._clock.now(), principal.subject, action, Outcome.SUCCESS, role.key, detail=detail
        )
        await tx.add_audit_event(event)

    async def _authorize(self, principal: Principal) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``roles.manage``."""
        require(await self._admin.authorize(principal, _MANAGE))


async def _role(tx: Transaction, role_id: int) -> CustomRole:
    """Return the custom role with ``role_id``, or raise ``role_not_found``."""
    role = await tx.get_custom_role(role_id)
    if role is None:
        raise DomainError(ErrorCode.ROLE_NOT_FOUND)
    return role


async def _current(tx: Transaction, role_id: int, version: int) -> CustomRole:
    """Return the role if it still has ``version``; raise ``role_changed`` otherwise."""
    role = await _role(tx, role_id)
    if role.version != version:
        raise DomainError(ErrorCode.ROLE_CHANGED)
    return role
