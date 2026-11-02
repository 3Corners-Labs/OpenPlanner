"""Use cases of managing staff in the bundled identity provider (ADR 0095).

Every operation first asks the matrix (``staff.manage`` or ``staff.reset_password``, organisation
level), then applies the rules of ``app.domain.staff_directory``: an admin sees and changes only
people with at least one grant inside their scope, never grants a role they could not hold, and
never edits or disables themselves. Each change is audited in the same step, with role names
and places and never a name or an address. The provider's own message is never passed on.

    invite(principal, new, grants)       staff.manage, mail with the link of ADR 0095 point 3
    set_grants(principal, subject, ...)  staff.manage
    set_enabled(principal, subject, on)  staff.manage, and the person works nowhere else
    send_reset(principal, subject)       staff.reset_password
    pending(principal)                   staff.view, invited people who never logged in
"""

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Grant, Permission, Principal, Role, Scope, scope_for
from app.domain.entitlements import Directory, parse_entitlements
from app.domain.errors import DomainError, ErrorCode
from app.domain.ports import Clock, StaffDirectory, UnitOfWork
from app.domain.staff_directory import (
    DirectoryError,
    DirectoryUser,
    NewStaffMember,
    StaffAction,
    in_scope,
    plan_entitlements,
    refusal_of,
)

_SECONDS_PER_DAY = 86_400

_ERRORS = {
    "conflict": ErrorCode.STAFF_ADDRESS_IN_USE,
    "mail": ErrorCode.STAFF_MAIL_FAILED,
    "not_found": ErrorCode.STAFF_NOT_FOUND,
}
"""Provider failure codes with a meaning of their own; every other one is "unavailable"."""


@dataclass(frozen=True)
class DirectorySettings:
    """The live settings of the directory, read at each call.

    Attributes:
        enabled: ``staff_directory_enabled``.
        prefix: The entitlement prefix (``oidc_entitlement_prefix``).
        invite_expiry_days: ``staff_invite_expiry_days``.
    """

    enabled: bool
    prefix: str
    invite_expiry_days: int


@dataclass(frozen=True)
class ManagedPerson:
    """A person of the provider as the admin may see them.

    Attributes:
        user: The provider's record.
        grants: Their grants inside the admin's scope, readable ones only.
        works_elsewhere: Whether they hold a readable grant outside the scope, or one this
            installation cannot read (they are then not disabled from here).
    """

    user: DirectoryUser
    grants: tuple[Grant, ...]
    works_elsewhere: bool


class StaffDirectoryService:
    """Application service of staff management; inert without a directory."""

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        directory: StaffDirectory | None,
        settings: Callable[[], DirectorySettings],
    ) -> None:
        """Store the collaborators; ``directory`` is ``None`` for a provider without one."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self._directory = directory
        self._settings = settings

    @property
    def available(self) -> bool:
        """Whether the provider has a directory and the superuser has not switched it off."""
        return self._directory is not None and self._settings().enabled

    async def reachable(self) -> bool:
        """Return whether the directory answers (for System status)."""
        return self._directory is not None and await self._directory.reachable()

    async def _ready(self, principal: Principal, permission: Permission) -> StaffDirectory:
        """Authorise, then return the directory, or refuse when there is none."""
        require(await self._admin.authorize_anywhere(principal, permission))
        if self._directory is None or not self._settings().enabled:
            raise DomainError(ErrorCode.STAFF_DIRECTORY_UNAVAILABLE)
        return self._directory

    async def _directory_of_places(self) -> Directory:
        """Return the organisations and groups with their IdP values."""
        async with self._uow.read() as tx:
            return await tx.get_directory()

    def _person(self, user: DirectoryUser, scope: Scope, places: Directory) -> ManagedPerson:
        """Split a person's readable grants into those in and outside ``scope``."""
        parsed = parse_entitlements(user.entitlements, self._settings().prefix, places)
        inside = tuple(sorted((g for g in parsed.grants if in_scope(g, scope)), key=_key))
        unread = len(user.entitlements) - len(parsed.grants) - parsed.ignored
        elsewhere = len(inside) < len(parsed.grants) or unread > 0
        if not scope.everything and any(g.role == Role.SUPERUSER.value for g in parsed.grants):
            return ManagedPerson(user, (), True)  # superusers are the superuser's alone
        return ManagedPerson(user, inside, elsewhere)

    async def _find(
        self, principal: Principal, directory: StaffDirectory, permission: Permission, subject: str
    ) -> ManagedPerson:
        """Return the person, or ``staff_not_found`` when the admin may not see them."""
        scope = scope_for(principal, permission)
        user = await _guard(directory.get_user(subject))
        if user is None:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        person = self._person(user, scope, await self._directory_of_places())
        if not person.grants:
            raise DomainError(ErrorCode.STAFF_NOT_FOUND)
        return person

    def _refuse_wishes(self, principal: Principal, scope: Scope, grants: Collection[Grant]) -> None:
        """Refuse a grant above the admin's rights or outside their scope."""
        superuser = Role.SUPERUSER in principal.roles
        for grant in grants:
            reason = refusal_of(scope, grant, superuser=superuser)
            if reason is not None:
                raise DomainError(ErrorCode.STAFF_GRANT_REFUSED, "grant")

    async def pending(self, principal: Principal) -> list[ManagedPerson]:
        """Return invited people in scope who have not logged in yet, by address."""
        require(await self._admin.authorize_anywhere(principal, Permission.STAFF_VIEW))
        if self._directory is None or not self._settings().enabled:
            return []
        scope = scope_for(principal, Permission.STAFF_MANAGE)
        if scope.is_empty:
            return []
        places = await self._directory_of_places()
        found = []
        for user in await _guard(self._directory.list_users()):
            person = self._person(user, scope, places)
            if not person.grants:
                continue
            async with self._uow.read() as tx:
                if await tx.get_staff(user.id) is not None:
                    continue
            found.append(person)
        return sorted(found, key=lambda p: p.user.email)

    async def person(self, principal: Principal, subject: str) -> ManagedPerson | None:
        """Return one person for their page, or ``None`` when the admin may not manage them."""
        if self._directory is None or not self._settings().enabled:
            return None
        if scope_for(principal, Permission.STAFF_MANAGE).is_empty:
            return None
        try:
            return await self._find(principal, self._directory, Permission.STAFF_MANAGE, subject)
        except DomainError:
            return None

    async def invite(
        self, principal: Principal, new: NewStaffMember, grants: Collection[Grant]
    ) -> None:
        """Create a person with ``grants`` and mail them the link that sets their password.

        Raises:
            DomainError: ``not_permitted``, ``staff_directory_unavailable``,
                ``invalid_configuration`` (no grant), ``staff_grant_refused``,
                ``staff_address_in_use`` or ``staff_mail_failed``.
        """
        directory = await self._ready(principal, Permission.STAFF_MANAGE)
        if not grants:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "grant")
        scope = scope_for(principal, Permission.STAFF_MANAGE)
        self._refuse_wishes(principal, scope, grants)
        settings = self._settings()
        values = _planned([], grants, scope, settings.prefix, await self._directory_of_places())
        user = await _guard(directory.create_user(new))
        await _guard(directory.set_entitlements(user.id, values))
        await self._change(principal, Permission.STAFF_MANAGE, grants, _created(grants))
        await _guard(
            directory.send_actions_email(
                user.id,
                [StaffAction.UPDATE_PASSWORD],
                settings.invite_expiry_days * _SECONDS_PER_DAY,
            )
        )

    async def set_grants(
        self, principal: Principal, subject: str, grants: Collection[Grant]
    ) -> None:
        """Replace the person's grants inside the admin's scope by ``grants``.

        Raises:
            DomainError: As ``invite``, and ``staff_not_found`` outside the scope.
        """
        directory = await self._ready(principal, Permission.STAFF_MANAGE)
        self._not_oneself(principal, subject)
        person = await self._find(principal, directory, Permission.STAFF_MANAGE, subject)
        scope = scope_for(principal, Permission.STAFF_MANAGE)
        self._refuse_wishes(principal, scope, grants)
        values = _planned(
            person.user.entitlements,
            grants,
            scope,
            self._settings().prefix,
            await self._directory_of_places(),
        )
        await _guard(directory.set_entitlements(subject, values))
        await self._change(principal, Permission.STAFF_MANAGE, grants, "directory.grants_set")

    async def set_enabled(self, principal: Principal, subject: str, enabled: bool) -> None:
        """Enable or disable a person who works in no place outside the admin's scope.

        Raises:
            DomainError: ``not_permitted`` (also for oneself or a person who works elsewhere),
                ``staff_not_found`` or ``staff_directory_unavailable``.
        """
        directory = await self._ready(principal, Permission.STAFF_MANAGE)
        self._not_oneself(principal, subject)
        person = await self._find(principal, directory, Permission.STAFF_MANAGE, subject)
        if person.works_elsewhere and not scope_for(principal, Permission.STAFF_MANAGE).everything:
            raise DomainError(ErrorCode.NOT_PERMITTED)
        await _guard(directory.set_enabled(subject, enabled))
        await self._change(
            principal,
            Permission.STAFF_MANAGE,
            person.grants,
            "directory.enabled" if enabled else "directory.disabled",
        )

    async def send_reset(self, principal: Principal, subject: str) -> None:
        """Mail a person a link to choose a new password.

        Raises:
            DomainError: ``not_permitted``, ``staff_not_found``, ``staff_directory_unavailable``
                or ``staff_mail_failed``.
        """
        directory = await self._ready(principal, Permission.STAFF_RESET_PASSWORD)
        person = await self._find(principal, directory, Permission.STAFF_RESET_PASSWORD, subject)
        await _guard(
            directory.send_actions_email(
                subject,
                [StaffAction.UPDATE_PASSWORD],
                self._settings().invite_expiry_days * _SECONDS_PER_DAY,
            )
        )
        await self._change(
            principal,
            Permission.STAFF_RESET_PASSWORD,
            person.grants,
            "directory.password_reset_sent",
        )

    @staticmethod
    def _not_oneself(principal: Principal, subject: str) -> None:
        """Refuse changes to oneself: a mistake would lock the admin out."""
        if principal.subject == subject:
            raise DomainError(ErrorCode.NOT_PERMITTED)

    async def _change(
        self,
        principal: Principal,
        permission: Permission,
        grants: Collection[Grant],
        detail: str,
    ) -> None:
        """Audit a change with role names and places; the organisation when there is one."""
        organisations = {g.organisation for g in grants if g.organisation}
        organisation = next(iter(organisations)) if len(organisations) == 1 else None
        async with self._uow.locked() as tx:
            await record_change(
                tx, self._clock.now(), principal, permission, organisation, None, detail
            )


def _key(grant: Grant) -> tuple[str, str, str]:
    """Sort grants by organisation, group and role."""
    return (grant.organisation or "", grant.group or "", grant.role)


def _created(grants: Collection[Grant]) -> str:
    """The audit detail of an invitation: the grants by key, never a name or address."""
    return "directory.created:" + ",".join(sorted(g.key for g in grants))


def _planned(
    existing: Collection[str],
    grants: Collection[Grant],
    scope: Scope,
    prefix: str,
    places: Directory,
) -> list[str]:
    """Plan the claim values, turning an unknown place into a refused grant."""
    try:
        return plan_entitlements(existing, grants, scope, prefix, places)
    except DirectoryError as error:
        raise DomainError(ErrorCode.STAFF_GRANT_REFUSED, "grant") from error


async def _guard[T](call: Awaitable[T]) -> T:
    """Await a provider call; turn its failure code into a domain error."""
    try:
        return await call
    except DirectoryError as error:
        raise DomainError(_ERRORS.get(error.code, ErrorCode.STAFF_DIRECTORY_UNAVAILABLE)) from error
