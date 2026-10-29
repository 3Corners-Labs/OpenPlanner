"""Embedding use cases: the websites on which an organisation's groups may appear (ADR 0035).

The list of allowed origins is kept per organisation with ``embedding.manage`` (organisation
admin and superuser). A group appears on those websites only when it is switched on in its
own settings (``Group.embedding_enabled``, ``group.edit``): the organisation decides where,
the group decides whether. Every change asks ``AdminService.authorize`` first and is audited
in the same transaction with counts, never the origins themselves.

    organisations   embedding.manage   the organisations in scope, with their counts
    view            embedding.manage   one organisation: origins and its groups
    save            embedding.manage   replace the origins; an empty list removes them
    allows          public             whether a group may be embedded on an origin
    widget_group    public             the group behind a widget URL, and the origin check
"""

from dataclasses import dataclass

from app.domain.admin import AdminService, record_change, require
from app.domain.authorization import Permission, Principal, Scope, scope_for
from app.domain.embedding import AllowedOrigins, parse_origins
from app.domain.errors import DomainError, ErrorCode
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, UnitOfWork
from app.domain.scheduling import Group, GroupSummary, Organisation

_PERMISSION = Permission.EMBEDDING_MANAGE


@dataclass(frozen=True)
class OrganisationEmbedding:
    """An organisation with its allowed origins and its groups, for the admin pages."""

    organisation: Organisation
    allowed: AllowedOrigins
    groups: tuple[GroupSummary, ...]

    @property
    def embedded_groups(self) -> tuple[GroupSummary, ...]:
        """The groups that are switched on for embedding."""
        return tuple(summary for summary in self.groups if summary.group.embedding_enabled)


def origins_detail(before: AllowedOrigins, after: AllowedOrigins) -> str:
    """Return the audit detail of a change: how many were added, removed and remain."""
    added = len(set(after.origins) - set(before.origins))
    removed = len(set(before.origins) - set(after.origins))
    return f"origins.update,added:{added},removed:{removed},total:{len(after.origins)}"


@dataclass(frozen=True)
class WidgetAccess:
    """The group behind a widget request and whether the request's origin may use it."""

    group: Group
    origin_allowed: bool


class EmbeddingService:
    """Application service for the allowed origins of organisations.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises changes and audits refusals.
        max_origins: How many origins one organisation may keep (setting, ADR 0042 class C).
        allow_local: Whether ``localhost`` origins are accepted (development and test only).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        admin: AdminService,
        *,
        max_origins: int,
        allow_local: bool,
    ) -> None:
        """Store the collaborators and the limits."""
        self._uow = uow
        self._clock = clock
        self._admin = admin
        self.max_origins = max_origins
        self.allow_local = allow_local

    async def organisations(self, principal: Principal) -> list[OrganisationEmbedding]:
        """Return every organisation ``principal`` may manage embedding for, by name."""
        result = []
        async with self._uow.read() as tx:
            for organisation in await tx.list_organisations(scope_for(principal, _PERMISSION)):
                groups = await tx.list_groups(Scope(organisations=frozenset({organisation.slug})))
                result.append(
                    OrganisationEmbedding(
                        organisation,
                        await tx.get_allowed_origins(organisation.id),
                        tuple(groups),
                    )
                )
        return result

    async def view(self, principal: Principal, slug: str) -> OrganisationEmbedding:
        """Return the organisation with ``slug``, its allowed origins and its groups.

        Raises:
            DomainError: ``not_permitted`` or ``organisation_not_found``.
        """
        await self._authorize(principal, slug)
        async with self._uow.read() as tx:
            organisation = await organisation_by_slug(tx, slug)
            groups = await tx.list_groups(Scope(organisations=frozenset({slug})))
            return OrganisationEmbedding(
                organisation,
                await tx.get_allowed_origins(organisation.id),
                tuple(groups),
            )

    async def save(self, principal: Principal, slug: str, text: str) -> bool:
        """Replace the origins with those in ``text`` (one per line); return whether it changed.

        Raises:
            DomainError: ``not_permitted``, ``organisation_not_found``, or
                ``invalid_configuration`` with field ``origin_{line}`` or ``origins_count``.
        """
        await self._authorize(principal, slug)
        allowed = parse_origins(text, allow_local_http=self.allow_local, limit=self.max_origins)
        async with self._uow.locked() as tx:
            organisation = await organisation_by_slug(tx, slug)
            current = await tx.get_allowed_origins(organisation.id)
            if allowed == current:
                return False
            await tx.save_allowed_origins(organisation.id, allowed)
            await record_change(
                tx,
                self._clock.now(),
                principal,
                _PERMISSION,
                slug,
                None,
                origins_detail(current, allowed),
            )
        return True

    async def allows(self, group: Group, origin: str | None) -> bool:
        """Return whether ``group`` may be embedded on a page of ``origin``.

        Both conditions hold: the group is switched on and the origin is on its organisation's
        list. No authorisation: this answers the public widget API (ADR 0035 §3).
        """
        if not group.embedding_enabled or origin is None:
            return False
        async with self._uow.read() as tx:
            return (await tx.get_allowed_origins(group.organisation_id)).allows(origin)

    async def widget_group(
        self, organisation_slug: str, group_slug: str, origin: str | None
    ) -> WidgetAccess:
        """Return the group of a widget request, and whether ``origin`` is on the list.

        A group that does not exist, belongs to another organisation or is not switched on
        for embedding is answered the same way, so the widget API reveals nothing about it.

        Raises:
            DomainError: ``group_not_found``.
        """
        async with self._uow.read() as tx:
            group = await tx.get_group(group_slug)
            organisation = await tx.get_organisation(organisation_slug)
            if (
                group is None
                or organisation is None
                or group.organisation_id != organisation.id
                or not group.embedding_enabled
            ):
                raise DomainError(ErrorCode.GROUP_NOT_FOUND)
            allowed = await tx.get_allowed_origins(organisation.id)
        return WidgetAccess(group, allowed.allows(origin))

    async def _authorize(self, principal: Principal, slug: str) -> None:
        """Raise ``not_permitted`` unless the matrix grants ``embedding.manage`` on ``slug``."""
        require(await self._admin.authorize(principal, _PERMISSION, organisation_slug=slug))
