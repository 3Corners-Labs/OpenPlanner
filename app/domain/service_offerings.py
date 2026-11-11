"""A service offering is the business name of the existing group (ADR 0121 points 1 and 8).

There is one source of truth: the group. This module is the one place that says the two are
the same thing; no table, no second identity.
"""

from dataclasses import dataclass

from app.domain.scheduling import Group


@dataclass(frozen=True)
class ServiceOffering:
    """The business view of a group.

    Attributes:
        id: The group's id.
        organisation_id: The owning organisation's id.
        slug: The group's slug (``/g/{slug}``).
        name: The group's name.
    """

    id: int
    organisation_id: int
    slug: str
    name: str


def from_group(group: Group) -> ServiceOffering:
    """Return the service offering that ``group`` is."""
    return ServiceOffering(group.id, group.organisation_id, group.slug, group.name)
