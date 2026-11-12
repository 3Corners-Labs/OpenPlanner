"""The named SQL definitions of what a flow uses and what is used by flows.

Skills and capacity pools ask where they are used; a flow uses what its published (current)
revision and its draft name, and nothing in older revisions (ADR 0121 point 3). Three questions
have one home here, each with its twin in ``app.domain.service_flows`` where the domain asks it
of objects instead of rows:

* *in effect* (``REVISION_IN_EFFECT``): the revision is the flow's current or draft one; the twin
  of ``revisions_in_effect``. It decides what a pool or skill is archived or edited under.
* *drawn on by a flow* (``drawn_on_by_flows``, ``skills_required_on_pool``): a flow that is not
  archived names the pool in a revision in effect; the twins are ``draws_on_pool`` and
  ``skills_required_on_pool``. It decides whether a pool may be archived and which skills it must
  keep guaranteeing.
* *referred to by any revision ever written* (``skills_referred_to``, ``pools_referred_to``,
  ``flows_kept``): the wider question that decides whether a configuration document deletes an
  object or archives it. History keeps every revision, so a skill or pool an old revision names,
  a person holds or a pool guarantees cannot be removed without losing history or grants.
"""

from sqlalchemy import ColumnElement, CompoundSelect, Select, and_, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    BookingRow,
    CapacityPoolSkillRow,
    FlowRevisionRow,
    FlowRevisionSkillRow,
    ServiceFlowRow,
    StaffSkillRow,
    SubjectFlowSkillRow,
)
from app.domain.service_flows import FlowState

REVISION_IN_EFFECT: ColumnElement[bool] = and_(
    FlowRevisionRow.flow_id == ServiceFlowRow.id,
    or_(
        FlowRevisionRow.revision == ServiceFlowRow.current_revision,
        FlowRevisionRow.revision == ServiceFlowRow.draft_revision,
    ),
)
"""Join condition: the revision row is the current or the draft revision of the flow row."""


def drawn_on_by_flows(pool_id: int) -> tuple[ColumnElement[bool], ...]:
    """Return the conditions that a revision in effect of a flow not archived names the pool.

    To be used after a join of the flow rows and ``REVISION_IN_EFFECT``.
    """
    return (
        FlowRevisionRow.capacity_pool_id == pool_id,
        ServiceFlowRow.state != FlowState.ARCHIVED.value,
    )


async def skills_required_on_pool(session: AsyncSession, pool_id: int) -> frozenset[int]:
    """Return the skills the flows drawing on the pool require, for the flow or a subject."""
    found: set[int] = set()
    for table in (FlowRevisionSkillRow, SubjectFlowSkillRow):
        found.update(
            await session.scalars(
                select(table.skill_id)
                .join(
                    FlowRevisionRow,
                    (FlowRevisionRow.flow_id == table.flow_id)
                    & (FlowRevisionRow.revision == table.revision),
                )
                .join(ServiceFlowRow, REVISION_IN_EFFECT)
                .where(*drawn_on_by_flows(pool_id))
            )
        )
    return frozenset(found)


def skills_referred_to() -> CompoundSelect[tuple[int | None]]:
    """Return the ids of the skills something refers to: a person, a pool or any revision."""
    return union(
        select(StaffSkillRow.skill_id),
        select(CapacityPoolSkillRow.skill_id),
        select(FlowRevisionSkillRow.skill_id),
        select(SubjectFlowSkillRow.skill_id),
    )


def pools_referred_to() -> Select[int | None]:
    """Return the ids of the pools any revision, old ones too, draws on."""
    return select(FlowRevisionRow.capacity_pool_id).where(
        FlowRevisionRow.capacity_pool_id.is_not(None)
    )


def flows_with_bookings() -> Select[int | None]:
    """Return the ids of the flows a booking refers to: the one definition of that question."""
    return select(BookingRow.flow_id).where(BookingRow.flow_id.is_not(None))


def flows_kept() -> CompoundSelect[tuple[int | None]]:
    """Return the ids of the flows a document archives instead of deleting.

    A flow that is or was active (state active or archived: a flow reaches active only by
    activation and never returns to draft) is history, and one a booking refers to is referred
    to; only a flow in state draft that no booking refers to may be deleted
    (``service_flows.check_flow_delete``).
    """
    return union(
        flows_with_bookings(),
        select(ServiceFlowRow.id).where(ServiceFlowRow.state != FlowState.DRAFT.value),
    )
