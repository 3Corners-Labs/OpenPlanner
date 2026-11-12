"""Which flow a new booking is made under (ADR 0121 decision 3, step 3a).

Pure functions over the flows of one service. A service has one *default* flow per entry path
and may offer more; a participant can name one by its code. ``resolve_default`` finds the
default of an entry path, ``resolve_explicit`` the flow a request named. An explicit code that
does not resolve is refused and never falls back to the default: the participant asked for
something specific, and silently giving them something else would hide a mistake.

Both functions only see the flows of the service they are given; a flow of another service or
organisation is therefore unknown here and refused like any other unknown code. The caller reads
the flows through the scoped port (``app.domain.flow_resolver``).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain import service_flows
from app.domain.errors import DomainError, ErrorCode
from app.domain.service_flows import (
    FlowRevision,
    FlowState,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
)


@dataclass(frozen=True)
class FlowRef:
    """The identity a booking is recorded under: a flow and the revision in effect."""

    flow_id: int
    revision: int


@dataclass(frozen=True)
class ResolvedFlow:
    """The flow a request resolved to, with what a booking needs of its revision now.

    Attributes:
        flow_id: The flow's id.
        code: The flow's code.
        revision: The number of the revision in effect (the flow's current revision).
        interaction_kind: What participant and staff do together; ``None`` only for a revision
            that never chose one (a published revision always has one).
        legacy: Whether the revision is the wrapper of a pre-flow service.
        capacity_pool_id: The pool the revision draws on (ADR 0122); a booking of a revision
            that is not legacy is placed on it.
        duration_minutes: The length of one appointment of the revision.
    """

    flow_id: int
    code: str
    revision: int
    interaction_kind: InteractionKind | None
    legacy: bool = False
    capacity_pool_id: int | None = None
    duration_minutes: int | None = None

    @property
    def ref(self) -> FlowRef:
        """Return the identity a booking stores."""
        return FlowRef(self.flow_id, self.revision)


def _live(flow: ServiceFlow) -> tuple[int, FlowRevision] | None:
    """Return the id and revision in effect of an active stored flow, else ``None``.

    A draft, an archived flow and one that was never stored (no id) have none.
    """
    if flow.state is not FlowState.ACTIVE or flow.current is None or flow.id is None:
        return None
    return flow.id, flow.current


def _resolved(flow: ServiceFlow, live: tuple[int, FlowRevision]) -> ResolvedFlow:
    """Return ``flow`` at its revision in effect (``live``, from ``_live``) as a resolved flow."""
    flow_id, revision = live
    return ResolvedFlow(
        flow_id,
        flow.code,
        revision.revision,
        revision.interaction_kind,
        revision.legacy,
        revision.capacity_pool_id,
        revision.duration_minutes,
    )


def pool_flows_bookable(mode: IntakeMode) -> bool:
    """Return whether any flow on a counted pool may take a new booking on the path ``mode`` now.

    The one reader of the rollback gate ``ROLLBACK_SAFE`` on the booking paths (ADR 0122 step 5),
    read when called: only appointments (on-demand intake on a pool is ADR 0121 step 7), and only
    while the gate is open. ``False`` lets a caller skip reading flows at all. Moving a booking
    that already sits on a pool does not ask: it stays on its pool (``pool_booking.Placements``).
    """
    return mode is IntakeMode.APPOINTMENT and bool(service_flows.ROLLBACK_SAFE)


def gate_allows(flow: ServiceFlow, mode: IntakeMode) -> bool:
    """Return whether a new booking on the entry path ``mode`` may be made under ``flow`` now.

    A legacy flow (and one without a current revision, which no rule resolves) always may; a
    flow that is not legacy draws on a counted pool and may when ``pool_flows_bookable``. A flow
    the gate holds back is, for the participant, exactly a flow that is not active: unknown when
    named, never the default.
    """
    if flow.current is None or flow.current.legacy:
        return True
    return pool_flows_bookable(mode)


def resolve_default(flows: Iterable[ServiceFlow], mode: IntakeMode) -> ResolvedFlow | None:
    """Return the active flow that is the default of the entry path ``mode``, else ``None``.

    ``None`` means no active flow is the default (none exists, or an administrator archived
    it): the caller records the booking without a flow.

    Raises:
        DomainError: ``flow_default_ambiguous`` when more than one is the default (the schema's
            unique index makes that impossible; the check is the second line).
    """
    found = [
        (flow, live)
        for flow in flows
        if (live := _live(flow)) is not None
        and mode in flow.default_for
        and mode in live[1].intake_modes
    ]
    if not found:
        return None
    if len(found) > 1:
        raise DomainError(ErrorCode.FLOW_DEFAULT_AMBIGUOUS, mode.value)
    return _resolved(*found[0])


def resolve_explicit(flows: Iterable[ServiceFlow], code: str, mode: IntakeMode) -> ResolvedFlow:
    """Return the active flow with ``code`` that offers the entry path ``mode``.

    An unknown code, a flow of another service (it is not among ``flows``), a draft or archived
    flow, and a flow that does not offer ``mode`` are all refused the same way, so the answer
    tells nothing about which of them it was. There is no fallback to the default.

    Raises:
        DomainError: ``flow_not_available`` (field ``flow``).
    """
    for flow in flows:
        live = _live(flow)
        if live is not None and flow.code == code and mode in live[1].intake_modes:
            return _resolved(flow, live)
    raise DomainError(ErrorCode.FLOW_NOT_AVAILABLE, "flow")
