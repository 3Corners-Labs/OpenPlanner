"""Maps a ``call_status`` row to the domain's ``CallStatus`` (ADR 0010).

One place for the mapping, because the transaction's reads and the slot closing of ADR 0117 both
build a status from a row.
"""

from app.adapters.db.models import CallStatusRow
from app.domain.calls import CallState, CallStatus, TaskState


def call_status_of(row: CallStatusRow) -> CallStatus:
    """Map a call status row."""
    return CallStatus(
        booking_id=row.booking_id,
        call=CallState(row.call),
        task=TaskState(row.task),
        participants=row.participants,
        host_joined=row.host_joined,
        host_joined_at=row.host_joined_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        task_created_at=row.task_created_at,
        connected_at=row.connected_at,
        updated_at=row.updated_at,
        fallback=row.fallback,
        peak_participants=row.peak_participants,
        guest_joined_at=row.guest_joined_at,
    )
