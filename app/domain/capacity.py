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
from app.domain.routing import validate_label
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
    """

    id: int | None
    organisation_id: int
    name: str
    mode: PoolMode
    guaranteed_skill_ids: frozenset[int] = field(default_factory=frozenset)
    archived_at: datetime | None = None
    group_id: int | None = None

    def __post_init__(self) -> None:
        """Validate the name (``pool_name``)."""
        validate_label(self.name, "pool_name")


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
) -> None:
    """Refuse saving ``pool`` given what is stored; the adapter loads the facts.

    ``stored`` is the pool with ``pool.id`` (``None`` for a new pool or an unknown id),
    ``group_organisation_id`` the organisation of ``pool.group_id`` (``None`` when there is no
    such group), ``skills`` the guaranteed skills by id as loaded, ``already_guaranteed`` what
    the stored pool guarantees and ``group_taken`` whether another pool wraps the group already.
    A pool's mode and group never change; a skill must be the organisation's own, and an
    archived one stays but is not newly added.

    Raises:
        DomainError: ``invalid_configuration`` (fields ``group_id``, ``mode``,
            ``guaranteed_skill_ids``), ``configuration_object_missing`` (``group_id``,
            ``pool_id``, ``guaranteed_skill_ids``), ``configuration_out_of_scope``
            (``group_id``, ``organisation_id``, ``guaranteed_skill_ids``) or
            ``configuration_object_exists`` (``group_id``).
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
    check_skills_of(
        pool.organisation_id,
        pool.guaranteed_skill_ids,
        skills,
        already_guaranteed,
        "guaranteed_skill_ids",
    )


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
