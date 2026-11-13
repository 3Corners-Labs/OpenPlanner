"""The one resolver every entry path uses to find the flow of a new booking (ADR 0121 step 3a).

The standalone page, the widget API and the direct-call page call ``resolve_flow`` inside the
transaction that stores the booking. It reads the service's own flows through the port and
applies ``app.domain.flow_resolution``. Moving or rescheduling does not resolve again: a
booking keeps the flow it was made under (``Booking.flow``).
"""

from app.domain.authorization import PUBLIC_READ
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import (
    ResolvedFlow,
    gate_allows,
    resolve_default,
    resolve_explicit,
)
from app.domain.ports import Transaction, UnitOfWork
from app.domain.routing import SUBJECT_CODE
from app.domain.service_flows import IntakeMode


async def resolve_flow(
    tx: Transaction, group_id: int, mode: IntakeMode, code: str | None = None
) -> ResolvedFlow | None:
    """Return the flow a new booking of ``group_id`` on the entry path ``mode`` is made under.

    Without ``code`` the active default flow of the path is used. With ``code`` that flow is
    used, or the request is refused: an explicit code never falls back to the default. The code
    is checked against the flow-code grammar before anything is read. Only the flows of
    ``group_id`` are ever read, so a flow of another service or organisation is unknown.

    A service without any flow rows (created by an older image during a rollback window, before
    the repair of ``ensure_default_flows``) resolves to ``None`` and the booking is recorded
    without a flow, exactly as before ADR 0121: a booking is never refused for missing flow
    rows. The same holds for an implicit request when no active flow is the default of the path
    (an administrator archived it); the service's own settings decide what is offered. Both
    cases record NULL: the column cannot tell "no flow rows" from "default archived" (ADR 0121).

    A flow on a counted pool takes part only while the rollback gate is open (``gate_allows``,
    ADR 0122 step 5, and since ADR 0121 step 7 on both paths); otherwise it counts as not
    active.

    Raises:
        DomainError: ``flow_not_available`` for an explicit code that does not resolve,
            ``flow_default_ambiguous`` when the schema's unique index failed.
    """
    if code is not None and not SUBJECT_CODE.fullmatch(code):
        raise DomainError(ErrorCode.FLOW_NOT_AVAILABLE, "flow")
    flows = [
        flow
        for flow in await tx.list_service_flows(PUBLIC_READ, group_id)
        if gate_allows(flow, mode)
    ]
    if code is not None:
        return resolve_explicit(flows, code, mode)
    return resolve_default(flows, mode)


async def flow_for(
    uow: UnitOfWork, group_slug: str, mode: IntakeMode, code: str | None
) -> ResolvedFlow | None:
    """Return the flow a page would book under, refusing an explicit ``code`` that fails.

    A page checks the flow it was opened with before it shows a form, so a participant never
    fills one in for a flow that cannot be booked (ADR 0121 step 3a). See ``resolve_flow``.

    Raises:
        DomainError: ``group_not_found`` or ``flow_not_available``.
    """
    async with uow.read() as tx:
        group = await tx.get_group(group_slug)
        if group is None or group.is_archived:
            raise DomainError(ErrorCode.GROUP_NOT_FOUND)
        return await resolve_flow(tx, group.id, mode, code)
