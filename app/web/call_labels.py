"""Call wording shared by web and worker: the agent's start time, admin labels and failure hints."""

from app.domain.booking import Booking, BookingKind
from app.domain.calls import CallState, EventKind, TaskState
from app.domain.scheduling import Group
from app.i18n.dates import format_clock, format_day_compact, local_day

AGENT_LOCALE = "nl"
"""Language of the start time shown to agents."""


def agent_start_label(booking: Booking, group: Group) -> str:
    """Return e.g. ``5 jan 2026, 09:00`` in the group's time zone."""
    day = format_day_compact(local_day(booking.start, group.tz), AGENT_LOCALE)
    return f"{day}, {format_clock(booking.start, group.tz, AGENT_LOCALE)}"


CALL_STATE_LABELS: dict[CallState, tuple[str, str]] = {
    CallState.NONE: ("call.state.none", ""),
    CallState.SCHEDULED: ("call.state.scheduled", "accent"),
    CallState.IN_PROGRESS: ("call.state.in_progress", "success"),
    CallState.ENDED: ("call.state.ended", ""),
    CallState.FAILED: ("call.state.failed", "danger"),
}
"""Catalogue key and chip tone per meeting state; the state is always written out."""

TASK_STATE_LABELS: dict[TaskState, tuple[str, str]] = {
    TaskState.NONE: ("call.task.none", ""),
    TaskState.QUEUED: ("call.task.queued", "warning"),
    TaskState.CONNECTED: ("call.task.connected", "success"),
    TaskState.ENDED: ("call.task.ended", ""),
    TaskState.FAILED: ("call.task.failed", "danger"),
    TaskState.CANCELLED: ("call.task.cancelled", "warning"),
}

EVENT_LABELS: dict[EventKind, str] = {
    EventKind.MEETING_CREATED: "call.event.meeting_created",
    EventKind.MEETING_FAILED: "call.event.meeting_failed",
    EventKind.MEETING_DELETED: "call.event.meeting_deleted",
    EventKind.JOIN_PAGE_OPENED: "call.event.join_page_opened",
    EventKind.JOIN_CLICKED: "call.event.join_clicked",
    EventKind.TASK_CREATED: "call.event.task_created",
    EventKind.TASK_FAILED: "call.event.task_failed",
    EventKind.TASK_CONNECTED: "call.event.task_connected",
    EventKind.TASK_ENDED: "call.event.task_ended",
    EventKind.TASK_END_REQUESTED: "call.event.task_end_requested",
    EventKind.END_NOT_POSSIBLE: "call.event.end_not_possible",
    EventKind.TASK_MARKED_CANCELLED: "call.event.task_marked_cancelled",
    EventKind.MEETING_STARTED: "call.event.meeting_started",
    EventKind.HOST_JOINED: "call.event.host_joined",
    EventKind.GUEST_JOINED: "call.event.guest_joined",
    EventKind.PARTICIPANT_LEFT: "call.event.participant_left",
    EventKind.MEETING_ENDED: "call.event.meeting_ended",
    EventKind.INTEGRATION_ERROR: "call.event.integration_error",
}

FAILURE_HINTS: dict[tuple[EventKind, str], str] = {
    (EventKind.MEETING_FAILED, "rejected_400"): "call.hint.meeting.rejected_400",
    (EventKind.MEETING_FAILED, "rejected_403"): "call.hint.meeting.rejected_403",
    (EventKind.MEETING_FAILED, "rejected_404"): "call.hint.meeting.rejected_404",
    (EventKind.TASK_FAILED, "meeting_missing"): "call.hint.task.meeting_missing",
}
"""What to check after a failure with a known meaning for that kind of event."""

CODE_HINTS: dict[str, str] = {
    "unauthorized": "call.hint.unauthorized",
    "rate_limited": "call.hint.unavailable",
    "server_error": "call.hint.unavailable",
    "unreachable": "call.hint.unavailable",
    "unexpected_response": "call.hint.unexpected_response",
}
"""What to check after a failure that means the same for every Webex API."""

DEFAULT_HINTS: dict[EventKind, str] = {
    EventKind.MEETING_FAILED: "call.hint.meeting.other",
    EventKind.TASK_FAILED: "call.hint.task.other",
    EventKind.INTEGRATION_ERROR: "call.hint.integration.other",
}
"""Fallback per failure event; ``end_not_possible`` has its own notice on the page."""


def failure_hint(kind: EventKind, code: str) -> str | None:
    """Return the catalogue key explaining a failure event, or ``None`` for other events."""
    if kind not in DEFAULT_HINTS:
        return None
    return FAILURE_HINTS.get((kind, code)) or CODE_HINTS.get(code) or DEFAULT_HINTS[kind]


KIND_LABELS: dict[BookingKind, str] = {
    BookingKind.SCHEDULED: "call.kind.scheduled",
    BookingKind.DIRECT: "call.kind.direct",
}
