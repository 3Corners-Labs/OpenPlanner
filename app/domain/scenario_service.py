"""Use cases for prepared closure scenarios (ADR 0037 §4).

Every operation names its ``ScenarioOwner`` and asks ``AdminService.authorize`` first:
``organisation.closure`` on an organisation, ``closure.manage`` on a group. Through a group an
admin sees the organisation's scenarios (to use them) and the group's own; only the owner's
own scenarios can be changed. Any other scenario is ``scenario_not_found``, so an id in a URL
never reveals another group's or organisation's scenario.

    list / get / image                     see own scenarios; a group also the organisation's
    save / save_contacts / delete          change own scenarios only
    set_image / remove_image               the scenario's one image (admins only until used)

Changes are audited in the same transaction with the scenario id and counts
(``closure.scenario.contacts,scenario:4,phone:1``), never names, texts or numbers.
``usable_scenario`` is the rule for which scenario a closure may start from
(``ClosureService``).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from app.domain.admin import AdminService, Authorization, record_change, require
from app.domain.authorization import Permission, Principal
from app.domain.contacts import ContactBlock
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import ImageFile, ImageText
from app.domain.lookups import group_by_slug
from app.domain.organisations import organisation_by_slug
from app.domain.ports import Clock, Transaction, UnitOfWork
from app.domain.scenarios import ClosureScenario, ScenarioOwner
from app.domain.scheduling import Group


@dataclass(frozen=True)
class ScenarioInput:
    """What an admin enters for a scenario: its name, notices and optional headings."""

    name: str
    notices: Mapping[str, str]
    headings: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioList:
    """The scenarios an owner sees.

    Attributes:
        own: The owner's own scenarios, which it can change.
        organisation: For a group: the organisation's scenarios, which it can only use.
    """

    own: list[ClosureScenario]
    organisation: list[ClosureScenario]


@dataclass(frozen=True)
class _Resolved:
    """An owner looked up: its organisation, its group (if any) and the audit target."""

    organisation_id: int
    organisation_slug: str
    group: Group | None
    permission: Permission

    @property
    def group_id(self) -> int | None:
        """The id of the owning group; ``None`` for an organisation."""
        return self.group.id if self.group else None


async def usable_scenario(
    tx: Transaction, group: Group, scenario_id: int, *, organisation_only: bool = False
) -> ClosureScenario:
    """Return the scenario ``group`` may start a closure from.

    That is one of its organisation's scenarios or, unless ``organisation_only`` (closing a
    whole organisation), one of the group's own.

    Raises:
        DomainError: ``scenario_not_found`` for any other scenario, without saying it exists.
    """
    scenario = await tx.get_scenario(scenario_id)
    if (
        scenario is None
        or scenario.organisation_id != group.organisation_id
        or (scenario.group_id is not None and (organisation_only or scenario.group_id != group.id))
    ):
        raise DomainError(ErrorCode.SCENARIO_NOT_FOUND)
    return scenario


class ScenarioService:
    """Application service for closure scenarios.

    Args:
        uow: Opens database transactions.
        clock: Current time, for audit events.
        admin: Authorises every operation and audits refusals.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, admin: AdminService) -> None:
        """Store the collaborators."""
        self._uow = uow
        self._clock = clock
        self._admin = admin

    async def list(self, principal: Principal, owner: ScenarioOwner) -> ScenarioList:
        """Return the owner's scenarios and, for a group, its organisation's.

        Raises:
            DomainError: ``not_permitted``, ``group_not_found`` or ``organisation_not_found``.
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.read() as tx:
            resolved = await _resolve(tx, owner, organisation)
            own = await tx.list_scenarios(resolved.organisation_id, resolved.group_id)
            shared = (
                await tx.list_scenarios(resolved.organisation_id, None) if resolved.group else []
            )
        return ScenarioList(own, shared)

    async def get(
        self, principal: Principal, owner: ScenarioOwner, scenario_id: int
    ) -> ClosureScenario:
        """Return a scenario the owner sees (its own or, for a group, the organisation's).

        Raises:
            DomainError: ``not_permitted`` or ``scenario_not_found``.
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.read() as tx:
            return await _visible(tx, await _resolve(tx, owner, organisation), scenario_id)

    async def image(
        self, principal: Principal, owner: ScenarioOwner, scenario_id: int
    ) -> ImageFile | None:
        """Return the image of a scenario the owner sees, for the admin's preview.

        Raises:
            DomainError: ``not_permitted`` or ``scenario_not_found``.
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.read() as tx:
            await _visible(tx, await _resolve(tx, owner, organisation), scenario_id)
            return await tx.get_scenario_image(scenario_id)

    async def save(
        self,
        principal: Principal,
        owner: ScenarioOwner,
        scenario: ScenarioInput,
        scenario_id: int | None = None,
    ) -> ClosureScenario:
        """Create a scenario, or change the name and texts of an own one.

        Raises:
            DomainError: ``not_permitted``, ``scenario_not_found`` or ``invalid_configuration``
                (``scenario_name``, ``notice_<locale>``, ``heading_<locale>``).
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.locked() as tx:
            resolved = await _resolve(tx, owner, organisation)
            if scenario_id is None:
                stored = await tx.add_scenario(
                    ClosureScenario(
                        None,
                        resolved.organisation_id,
                        resolved.group_id,
                        scenario.name,
                        scenario.notices,
                        scenario.headings,
                    )
                )
                action = "create"
            else:
                current = await _own(tx, resolved, scenario_id)
                stored = ClosureScenario(
                    current.id,
                    current.organisation_id,
                    current.group_id,
                    scenario.name,
                    scenario.notices,
                    scenario.headings,
                    current.contacts,
                    current.image,
                )
                await tx.save_scenario(stored)
                action = "update"
            await self._audit(tx, principal, resolved, f"closure.scenario.{action},{_ref(stored)}")
        return stored

    async def save_contacts(
        self, principal: Principal, owner: ScenarioOwner, scenario_id: int, block: ContactBlock
    ) -> None:
        """Give an own scenario a contact block; an empty one means "the group's at the start".

        Raises:
            DomainError: ``not_permitted`` or ``scenario_not_found``.
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.locked() as tx:
            resolved = await _resolve(tx, owner, organisation)
            current = await _own(tx, resolved, scenario_id)
            stored = None if block.is_empty else block
            await tx.save_scenario(replace(current, contacts=stored))
            counts = stored.counts if stored else "remove"
            detail = f"closure.scenario.contacts,{_ref(current)},{counts}"
            await self._audit(tx, principal, resolved, detail)

    async def delete(self, principal: Principal, owner: ScenarioOwner, scenario_id: int) -> None:
        """Delete an own scenario and its image; running closures keep their copies.

        Raises:
            DomainError: ``not_permitted`` or ``scenario_not_found``.
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.locked() as tx:
            resolved = await _resolve(tx, owner, organisation)
            current = await _own(tx, resolved, scenario_id)
            await tx.delete_scenario(scenario_id)
            await self._audit(tx, principal, resolved, f"closure.scenario.delete,{_ref(current)}")

    async def set_image(
        self,
        principal: Principal,
        owner: ScenarioOwner,
        scenario_id: int,
        text: ImageText,
        image: ImageFile | None,
    ) -> None:
        """Give an own scenario an image, or change only the text of the one it has.

        Raises:
            DomainError: ``not_permitted``, ``scenario_not_found`` or ``invalid_configuration``
                with ``image`` when there is neither a new nor a stored file.
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.locked() as tx:
            resolved = await _resolve(tx, owner, organisation)
            current = await _own(tx, resolved, scenario_id)
            if image is None and current.image is None:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "image")
            await tx.save_scenario_image(scenario_id, text, image)
            detail = f"closure.scenario.image,{_ref(current)},file:{int(image is not None)}"
            await self._audit(tx, principal, resolved, detail)

    async def remove_image(
        self, principal: Principal, owner: ScenarioOwner, scenario_id: int
    ) -> None:
        """Remove the image of an own scenario.

        Raises:
            DomainError: ``not_permitted`` or ``scenario_not_found``.
        """
        organisation = await self._authorize(principal, owner)
        async with self._uow.locked() as tx:
            resolved = await _resolve(tx, owner, organisation)
            current = await _own(tx, resolved, scenario_id)
            await tx.remove_scenario_image(scenario_id)
            detail = f"closure.scenario.image.remove,{_ref(current)}"
            await self._audit(tx, principal, resolved, detail)

    # --- helpers --------------------------------------------------------------------------

    async def _authorize(self, principal: Principal, owner: ScenarioOwner) -> str:
        """Authorise the owner's permission; return the slug of its organisation."""
        authorization: Authorization
        if owner.group is not None:
            authorization = await self._admin.authorize(
                principal, Permission.CLOSURE_MANAGE, group_slug=owner.group
            )
        else:
            authorization = await self._admin.authorize(
                principal, Permission.ORGANISATION_CLOSURE, organisation_slug=owner.organisation
            )
        return require(authorization).target.organisation or ""

    async def _audit(
        self, tx: Transaction, principal: Principal, resolved: _Resolved, detail: str
    ) -> None:
        """Record a scenario change at the owner's level, in the same transaction."""
        await record_change(
            tx,
            self._clock.now(),
            principal,
            resolved.permission,
            resolved.organisation_slug,
            resolved.group.slug if resolved.group else None,
            detail,
        )


async def _resolve(tx: Transaction, owner: ScenarioOwner, organisation_slug: str) -> _Resolved:
    """Look up the owner's organisation (its slug from the authorisation) and group.

    Raises:
        DomainError: ``group_not_found`` or ``organisation_not_found``.
    """
    if owner.group is not None:
        group = await group_by_slug(tx, owner.group)
        return _Resolved(group.organisation_id, organisation_slug, group, Permission.CLOSURE_MANAGE)
    organisation = await organisation_by_slug(tx, organisation_slug)
    return _Resolved(organisation.id, organisation.slug, None, Permission.ORGANISATION_CLOSURE)


async def _visible(tx: Transaction, resolved: _Resolved, scenario_id: int) -> ClosureScenario:
    """Return a scenario the owner sees: its own or, for a group, the organisation's."""
    scenario = await tx.get_scenario(scenario_id)
    if (
        scenario is None
        or scenario.organisation_id != resolved.organisation_id
        or scenario.group_id not in {resolved.group_id, None}
    ):
        raise DomainError(ErrorCode.SCENARIO_NOT_FOUND)
    return scenario


async def _own(tx: Transaction, resolved: _Resolved, scenario_id: int) -> ClosureScenario:
    """Return a scenario the owner may change: only its own."""
    scenario = await _visible(tx, resolved, scenario_id)
    if scenario.group_id != resolved.group_id:
        raise DomainError(ErrorCode.SCENARIO_NOT_FOUND)
    return scenario


def _ref(scenario: ClosureScenario) -> str:
    """Return the scenario part of an audit detail: ``scenario:4``."""
    return f"scenario:{scenario.id}"
