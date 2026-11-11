"""The capacity pool and flows every group has from now on (ADR 0121 point 8, step 2e).

Migration 0109 gave every existing group a legacy capacity pool and its default flows. This
module is the same rule for every group created afterwards (in the portal, by a configuration
import, or by an older image during a rollback window) and for a group that starts offering the
direct call later. The rule is purely additive and idempotent: it never changes or deletes an
existing pool or flow, so it can run after every write and as a repair.

A ``direct`` flow that exists stays when the group stops offering the direct call: whether a direct
page is open is decided by the weekly schedule rules, never by the flow.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.domain.capacity import CapacityPool, PoolMode
from app.domain.routing import MAX_LABEL_LENGTH
from app.domain.scheduling import DispatchKind as GroupDispatchKind
from app.domain.scheduling import Offer, ScheduleRule
from app.domain.service_flows import (
    DispatchKind,
    FlowState,
    IntakeMode,
    ServiceFlow,
    legacy_revision,
)

APPOINTMENT_CODE = "appointment"
"""The code of the legacy flow that takes appointments."""
DIRECT_CODE = "direct"
"""The code of the legacy flow that takes direct calls."""
UNSTORED_POOL = 0
"""The pool id a revision carries until ``bind_pool`` names the stored pool."""


@dataclass(frozen=True)
class DefaultFlowSet:
    """A legacy pool and legacy flows, as wanted or as still missing.

    Attributes:
        pool: The legacy group pool, ``None`` when it exists already (in a ``missing_defaults``
            result).
        flows: The legacy flows, each with revision 1 (not yet published); their revisions
            name ``UNSTORED_POOL`` until ``bind_pool`` is applied.
    """

    pool: CapacityPool | None
    flows: tuple[ServiceFlow, ...]

    @property
    def empty(self) -> bool:
        """Return whether there is nothing to create."""
        return self.pool is None and not self.flows


@dataclass(frozen=True)
class StoredFlow:
    """What the rule needs to know about a flow that exists: code, state and default paths."""

    code: str
    state: FlowState
    default_for: frozenset[IntakeMode]


def stored_flow(flow: ServiceFlow) -> StoredFlow:
    """Return the facts of an existing ``flow`` that ``missing_defaults`` reads."""
    return StoredFlow(flow.code, flow.state, flow.default_for)


def _label(name: str) -> str:
    """Return ``name`` as a label: stripped, cut to 80 characters, never empty (migration 0109)."""
    return name.strip()[:MAX_LABEL_LENGTH].strip() or "-"


def offers_direct(rules: Iterable[ScheduleRule]) -> bool:
    """Return whether a weekly rule offers direct calls (``direct`` or ``both``) with agents.

    The validity dates of a rule do not matter: the flow is configuration, and whether a direct
    page is open today stays decided by the rules, so the result never depends on the day.
    """
    return any(
        rule.window.offer in (Offer.DIRECT, Offer.BOTH) and rule.window.capacity > 0
        for rule in rules
    )


def flow_dispatch(kind: GroupDispatchKind) -> DispatchKind:
    """Return the flow dispatch of a group's dispatch kind: own queue, else contact center."""
    if kind is GroupDispatchKind.OPENPLANNER_QUEUE:
        return DispatchKind.OWN_QUEUE
    return DispatchKind.CONTACT_CENTER


def default_flow_set(
    group_id: int,
    organisation_id: int,
    name: str,
    slot_minutes: int,
    dispatch: GroupDispatchKind,
    offers_direct: bool,
    archived: bool,
) -> DefaultFlowSet:
    """Return the legacy pool and flows a group is to have.

    Args:
        group_id: The group.
        organisation_id: Its organisation.
        name: Its display name; the pool name and the labels are this name cut to 80 characters.
        slot_minutes: Its appointment length, the duration of both flows.
        dispatch: Its dispatch kind; the own queue stays the own queue, everything else is the
            contact center.
        offers_direct: Whether one of its weekly rules offers direct calls (``direct`` or
            ``both``) with a capacity above zero; the validity dates of a rule do not matter.
        archived: Whether the group is archived; its flows are then archived, not active.

    Returns:
        The pool (mode ``legacy_group``) and the ``appointment`` flow, plus the ``direct`` flow
        when ``offers_direct``; every flow is the default of its entry path.
    """
    label = _label(name)
    pool = CapacityPool(
        None, organisation_id, label, PoolMode.LEGACY_GROUP, frozenset(), None, group_id
    )
    state = FlowState.ARCHIVED if archived else FlowState.ACTIVE
    offered = [(APPOINTMENT_CODE, IntakeMode.APPOINTMENT)]
    if offers_direct:
        offered.append((DIRECT_CODE, IntakeMode.ON_DEMAND))
    flows = tuple(
        ServiceFlow(
            id=None,
            group_id=group_id,
            code=code,
            state=state,
            default_for=frozenset({intake}),
            current=legacy_revision(
                labels={"nl": label, "en": label},
                intake_mode=intake,
                duration_minutes=slot_minutes,
                capacity_pool_id=UNSTORED_POOL,
                dispatch=flow_dispatch(dispatch),
            ),
        )
        for code, intake in offered
    )
    return DefaultFlowSet(pool, flows)


def missing_defaults(
    wanted: DefaultFlowSet, pool_exists: bool, existing: Iterable[StoredFlow]
) -> DefaultFlowSet:
    """Return what of ``wanted`` is still to be created; nothing existing is changed.

    A pool exists when the group has a pool of its own (whatever its name); a flow exists when
    the group has a flow with its code in any state. A flow that would be the default of an entry
    path that another active flow already holds is created without that default, so the unique
    index of the default path cannot refuse it.
    """
    stored = list(existing)
    held = {path for flow in stored if flow.state is FlowState.ACTIVE for path in flow.default_for}
    codes = {flow.code for flow in stored}
    created: list[ServiceFlow] = []
    for flow in wanted.flows:
        if flow.code in codes:
            continue
        if flow.state is FlowState.ACTIVE and flow.default_for & held:
            flow = replace(flow, default_for=flow.default_for - held)
        created.append(flow)
    return DefaultFlowSet(None if pool_exists else wanted.pool, tuple(created))


def bind_pool(defaults: DefaultFlowSet, pool_id: int) -> DefaultFlowSet:
    """Return ``defaults`` with every flow revision drawing on the stored pool ``pool_id``."""
    return replace(
        defaults,
        flows=tuple(
            replace(flow, current=replace(flow.current, capacity_pool_id=pool_id))
            for flow in defaults.flows
            if flow.current is not None
        ),
    )
