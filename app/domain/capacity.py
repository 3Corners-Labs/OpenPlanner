"""Capacity pools: whose time a flow draws on and what the pool guarantees (ADR 0121 point 6).

A pool is either a *legacy group* pool (it wraps one group's present staffing and is never
shared) or a *counted* pool. A counted pool says which skills every one of its units holds;
a flow is only bookable against a pool that guarantees what the flow requires.
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import MAX_LABEL_LENGTH


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
        name: Admin-facing name, 1 to 80 characters after stripping.
        mode: Legacy group pool or counted pool.
        guaranteed_skill_ids: Skills that every counted unit of the pool holds.
        archived_at: When it was archived.
    """

    id: int | None
    organisation_id: int
    name: str
    mode: PoolMode
    guaranteed_skill_ids: frozenset[int] = field(default_factory=frozenset)
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the name (``pool_name``)."""
        stripped = self.name.strip()
        if not stripped or len(stripped) > MAX_LABEL_LENGTH:
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "pool_name")


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


def shareable(pool: CapacityPool) -> bool:
    """Return whether several flows may share the pool.

    A legacy pool wraps one group's present staffing and is not shared.
    """
    return pool.mode is not PoolMode.LEGACY_GROUP
