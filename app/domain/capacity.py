"""Capacity pools: whose time a flow draws on and what the pool guarantees (ADR 0121 point 6).

A pool is either a *legacy group* pool (it wraps one group's present staffing and is never
shared) or a *counted* pool. A counted pool says which skills every one of its units holds;
a flow is only bookable against a pool that guarantees what the flow requires.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode, missing_object, outside_organisation
from app.domain.routing import SUBJECT_CODE, validate_label
from app.domain.skills import Skill, check_skills_of


class PoolMode(StrEnum):
    """How a pool counts its capacity."""

    LEGACY_GROUP = "legacy_group"
    COUNTED = "counted"


@dataclass(frozen=True)
class CapacityPool:
    """One capacity pool of an organisation.

    Attributes:
        id: Database id, ``None`` before it is stored.
        organisation_id: The owning organisation.
        name: Admin-facing name, 1 to 80 characters after stripping, no control characters.
        mode: Legacy group pool or counted pool.
        guaranteed_skill_ids: Skills that every counted unit of the pool holds.
        archived_at: When it was archived.
        group_id: The group a legacy group pool wraps; ``None`` for a counted pool.
        code: Stable code of a counted pool, the grammar of a skill code, fixed after creation.
            ``None`` for a legacy pool, which is identified by its service, and for a stored
            counted pool written before the column existed (it is never derived: a rename
            would change it and two pools could show one); a new counted pool needs a code.
    """

    id: int | None
    organisation_id: int
    name: str
    mode: PoolMode
    guaranteed_skill_ids: frozenset[int] = field(default_factory=frozenset)
    archived_at: datetime | None = None
    group_id: int | None = None
    code: str | None = None

    def __post_init__(self) -> None:
        """Validate the name (``pool_name``) and the code (``pool_code``)."""
        validate_label(self.name, "pool_name")
        if self.code is None:
            # A stored counted pool may lack a code (a row from before 0110); a new one may not.
            if self.mode is PoolMode.COUNTED and self.id is None:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, "pool_code")
        elif self.mode is not PoolMode.COUNTED or not SUBJECT_CODE.fullmatch(self.code):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "pool_code")


def unguaranteed(pool: CapacityPool, required: Collection[int]) -> frozenset[int]:
    """Return the required skill ids the pool does not guarantee."""
    return frozenset(required) - pool.guaranteed_skill_ids


def guarantees(pool: CapacityPool, required: Collection[int]) -> bool:
    """Return whether the pool guarantees every required skill.

    Every counted unit holds all guaranteed skills; the guarantee is never the union of
    several people's skills, so two people holding X and Y do not make a pool that
    guarantees only X guarantee both.
    """
    return not unguaranteed(pool, required)


def check_pool_save(
    pool: CapacityPool,
    stored: CapacityPool | None,
    group_organisation_id: int | None,
    skills: Mapping[int, Skill],
    already_guaranteed: frozenset[int],
    *,
    group_taken: bool = False,
    same_code_exists: bool = False,
    same_name_exists: bool = False,
    still_required: frozenset[int] = frozenset(),
) -> None:
    """Refuse saving ``pool`` given what is stored; the adapter loads the facts.

    ``stored`` is the pool with ``pool.id`` (``None`` for a new pool or an unknown id),
    ``group_organisation_id`` the organisation of ``pool.group_id`` (``None`` when there is no
    such group), ``skills`` the guaranteed skills by id as loaded, ``already_guaranteed`` what
    the stored pool guarantees and ``group_taken`` whether another pool wraps the group already.
    ``same_code_exists`` says another pool of the organisation has the code, ``same_name_exists``
    that another counted pool that is not archived has the name (compared case-insensitively
    after stripping; only asked of a new pool or a changed name, so a duplicate that was stored
    earlier does not block editing the rest of the pool). ``still_required`` are the skills that
    the flows, not archived, drawing on the stored pool by their current or draft revision still
    require (``app.domain.service_flows.skills_required_on_pool``): a pool cannot stop
    guaranteeing one of them, because publishing or activating that flow would then be refused
    only afterwards, and a flow already live would draw on a pool that cannot serve it.
    A pool's mode and group never change; a skill must be the organisation's own, and an
    archived one stays but is not newly added.

    Raises:
        DomainError: ``invalid_configuration`` (fields ``group_id``, ``mode``,
            ``guaranteed_skill_ids``), ``configuration_object_missing`` (``group_id``,
            ``pool_id``, ``guaranteed_skill_ids``), ``configuration_out_of_scope``
            (``group_id``, ``organisation_id``, ``guaranteed_skill_ids``) or
            ``configuration_object_exists`` (``group_id``), ``configuration_object_in_use``
            (``guaranteed_skill_ids``, a skill a flow still requires), ``slug_taken``
            (``pool_code``, ``pool_name``) or
            ``invalid_configuration`` (``pool_code``) for a code that differs from the stored
            one: it is fixed (not asked of a stored pool that has none).
    """
    if (pool.mode is PoolMode.LEGACY_GROUP) != (pool.group_id is not None):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "group_id")
    if pool.group_id is not None:
        if group_organisation_id is None:
            raise missing_object("group_id")
        if group_organisation_id != pool.organisation_id:
            raise outside_organisation("group_id")
        if group_taken:
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS, "group_id")
    if pool.id is not None:
        if stored is None:
            raise missing_object("pool_id")
        if stored.organisation_id != pool.organisation_id:
            raise outside_organisation("organisation_id")
        if (stored.mode, stored.group_id) != (pool.mode, pool.group_id):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "mode")
        if stored.code is not None and stored.code != pool.code:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "pool_code")
    # A stored pool without a code keeps none (the adapter never writes one), so its code and the
    # duplicate check do not apply.
    codeless = stored is not None and stored.code is None
    if same_code_exists and not codeless:
        raise DomainError(ErrorCode.SLUG_TAKEN, "pool_code")
    renamed = stored is None or same_name(stored.name) != same_name(pool.name)
    if same_name_exists and renamed and pool.mode is PoolMode.COUNTED:
        raise DomainError(ErrorCode.SLUG_TAKEN, "pool_name")
    if pool.id is not None and still_required - pool.guaranteed_skill_ids:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "guaranteed_skill_ids")
    check_skills_of(
        pool.organisation_id,
        pool.guaranteed_skill_ids,
        skills,
        already_guaranteed,
        "guaranteed_skill_ids",
    )


def same_name(name: str) -> str:
    """Return ``name`` as pool names are compared: stripped and case-folded."""
    return name.strip().casefold()


@dataclass(frozen=True)
class PoolFlows:
    """The flows that draw on a pool, as far as the caller may see them.

    Attributes:
        flows: Ids of the not archived flows of groups inside the caller's scope, by id.
        hidden: How many further flows, of groups outside the scope, draw on the pool; only the
            count is given, so a screen can say "and 2 flows of other services".
    """

    flows: tuple[int, ...] = ()
    hidden: int = 0


def check_pool_archive(flow_codes: Collection[str]) -> None:
    """Refuse archiving a pool while a flow that is not archived draws on it.

    ``flow_codes`` are the codes of the flows whose current *or* draft revision names the pool
    (the screen can name them). A draft counts: publishing it would otherwise bring an archived
    pool live.

    Raises:
        DomainError: ``configuration_object_in_use`` (field ``capacity_pool``).
    """
    if flow_codes:
        raise DomainError(ErrorCode.CONFIGURATION_OBJECT_IN_USE, "capacity_pool")
