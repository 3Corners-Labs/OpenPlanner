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
    """

    flow_id: int
    code: str
    revision: int
    interaction_kind: InteractionKind | None
    legacy: bool = False

    @property
    def ref(self) -> FlowRef:
        """Return the identity a booking stores."""
        return FlowRef(self.flow_id, self.revision)


def _live(flow: ServiceFlow) -> FlowRevision | None:
    """Return the revision in effect of an active flow, else ``None`` (draft, archived)."""
    if flow.state is not FlowState.ACTIVE or flow.current is None or flow.id is None:
        return None
    return flow.current


def _resolved(flow: ServiceFlow, revision: FlowRevision) -> ResolvedFlow:
    """Return ``flow`` at ``revision`` as a resolved flow (``flow.id`` is set, see ``_live``)."""
    assert flow.id is not None  # noqa: S101 - narrowed for the type checker; _live checked it
    return ResolvedFlow(
        flow.id, flow.code, revision.revision, revision.interaction_kind, revision.legacy
    )


def resolve_default(flows: Iterable[ServiceFlow], mode: IntakeMode) -> ResolvedFlow:
    """Return the active flow that is the default of the entry path ``mode``.

    Raises:
        DomainError: ``flow_default_missing`` when no active flow is the default of ``mode``,
            ``flow_default_ambiguous`` when more than one is (the schema's unique index makes
            that impossible; the check is the second line).
    """
    found = [
        (flow, revision)
        for flow in flows
        if (revision := _live(flow)) is not None
        and mode in flow.default_for
        and mode in revision.intake_modes
    ]
    if not found:
        raise DomainError(ErrorCode.FLOW_DEFAULT_MISSING, mode.value)
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
        revision = _live(flow)
        if revision is not None and flow.code == code and mode in revision.intake_modes:
            return _resolved(flow, revision)
    raise DomainError(ErrorCode.FLOW_NOT_AVAILABLE, "flow")
