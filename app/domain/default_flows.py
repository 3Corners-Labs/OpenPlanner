"""The capacity pool and flows every group has from now on (ADR 0121 point 8, step 2e).

Migration 0109 gave every existing group a legacy capacity pool and its default flows. This
module is the same rule for every group created afterwards (in the portal, by a configuration
import, or by an older image during a rollback window) and for a group that starts offering the
direct call later. The rule is purely additive and idempotent: it never changes or deletes an
existing pool or flow, so it can run after every write and as a repair.

A ``direct`` flow that exists stays when the group stops offering the direct call: whether a direct
page is open is decided by the weekly schedule rules, never by the flow.

What a legacy flow is: a legacy pool and its legacy flows are a *snapshot* taken when they are
created (the service's name, appointment length, dispatch and archived state of that moment). They
never follow a later change of any of these. While a flow's current revision is ``legacy``, the
service's own settings and its archived state stay the source of truth for behaviour; the resolver
of step 3 reads the group first and uses a legacy flow only as the identity a booking is recorded
under. A legacy revision comes into existence only through ``bind_pool``, with a stored pool.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.domain.capacity import CapacityPool, PoolMode
from app.domain.routing import MAX_LABEL_LENGTH
from app.domain.scheduling import DispatchKind as GroupDispatchKind
from app.domain.service_flows import (
    DispatchKind,
    FlowRevision,
    FlowState,
    IntakeMode,
    ServiceFlow,
    legacy_revision,
)

APPOINTMENT_CODE = "appointment"
"""The code of the legacy flow that takes appointments."""
DIRECT_CODE = "direct"
"""The code of the legacy flow that takes direct calls."""


@dataclass(frozen=True)
class FlowTemplate:
    """What a legacy flow needs before its pool is stored; it has no revision yet.

    Attributes:
        code: The flow code, ``APPOINTMENT_CODE`` or ``DIRECT_CODE``.
        state: Active, or archived for an archived group.
        default_for: The entry paths the flow is the default of.
        intake_mode: The one entry path the legacy revision offers.
        label: The label of revision 1, in every locale.
        duration_minutes: The appointment length.
        dispatch: Own queue or contact center.
    """

    code: str
    state: FlowState
    default_for: frozenset[IntakeMode]
    intake_mode: IntakeMode
    label: str
    duration_minutes: int
    dispatch: DispatchKind

    def revision(self, pool_id: int) -> FlowRevision:
        """Return revision 1 drawing on the stored pool ``pool_id``; the only way one is made."""
        return legacy_revision(
            labels={"nl": self.label, "en": self.label},
            intake_mode=self.intake_mode,
            duration_minutes=self.duration_minutes,
            capacity_pool_id=pool_id,
            dispatch=self.dispatch,
        )

    def build(self, group_id: int, pool_id: int) -> ServiceFlow:
        """Return the flow, not yet stored, with its revision on the stored pool ``pool_id``."""
        return ServiceFlow(
            id=None,
            group_id=group_id,
            code=self.code,
            state=self.state,
            default_for=self.default_for,
            current=self.revision(pool_id),
        )


@dataclass(frozen=True)
class DefaultFlowSet:
    """A legacy pool and the templates of legacy flows, as wanted or as still missing.

    Attributes:
        group_id: The group.
        pool: The legacy group pool, ``None`` when it exists already (in a ``missing_defaults``
            result).
        flows: The templates of the legacy flows; no revision exists until ``bind_pool``.
    """

    group_id: int
    pool: CapacityPool | None
    flows: tuple[FlowTemplate, ...]

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
        offers_direct: Whether one of its weekly rules has ``window.offers_direct`` (the
            migration's rule: direct or both, with agents, whatever the validity dates).
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
        FlowTemplate(
            code, state, frozenset({intake}), intake, label, slot_minutes, flow_dispatch(dispatch)
        )
        for code, intake in offered
    )
    return DefaultFlowSet(group_id, pool, flows)


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
    created: list[FlowTemplate] = []
    for flow in wanted.flows:
        if flow.code in codes:
            continue
        if flow.state is FlowState.ACTIVE and flow.default_for & held:
            flow = replace(flow, default_for=flow.default_for - held)
        created.append(flow)
    return DefaultFlowSet(wanted.group_id, None if pool_exists else wanted.pool, tuple(created))


def bind_pool(defaults: DefaultFlowSet, pool_id: int) -> tuple[ServiceFlow, ...]:
    """Return the flows of ``defaults`` with revision 1 drawing on the stored pool ``pool_id``.

    The one place a legacy revision is built: no revision exists without a real pool id.
    """
    return tuple(template.build(defaults.group_id, pool_id) for template in defaults.flows)
