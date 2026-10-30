"""Call wording shared by web and worker: the agent's start time, admin labels and failure hints."""

from app.domain.booking import Booking, BookingKind
from app.domain.calls import CallState, EventKind, TaskState
from app.domain.job_plan import JobStatus, JobType
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
    EventKind.TRIAL_CREATED: "call.event.trial_created",
    EventKind.TRIAL_JOINED: "call.event.trial_joined",
    EventKind.TRIAL_FAILED: "call.event.trial_failed",
    EventKind.TRIAL_DELETED: "call.event.trial_deleted",
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


UNEXPLAINED_HINTS: dict[tuple[EventKind, str], str] = {
    (EventKind.MEETING_FAILED, "rejected_400"): "call.hint.meeting.rejected_400.unexplained",
}
"""The hint when the event stores no provider reason (ADR 0068): the usual hint points at
the reason above the event, which is not there for events of an older version."""


def failure_hint(kind: EventKind, code: str, *, explained: bool = True) -> str | None:
    """Return the catalogue key explaining a failure event, or ``None`` for other events.

    ``explained`` says whether the event carries the provider's reason; without it the hint
    must not claim the answer stands above.
    """
    if kind not in DEFAULT_HINTS:
        return None
    if not explained and (kind, code) in UNEXPLAINED_HINTS:
        return UNEXPLAINED_HINTS[(kind, code)]
    return FAILURE_HINTS.get((kind, code)) or CODE_HINTS.get(code) or DEFAULT_HINTS[kind]


JOB_LABELS: dict[JobType, str] = {
    JobType.CREATE_VIDEO_ROOM: "audit.job.create_video_room",
    JobType.DELETE_VIDEO_ROOM: "audit.job.delete_video_room",
    JobType.DELETE_TRIAL_CALL: "audit.job.delete_trial_call",
    JobType.SEND_CONFIRMATION: "audit.job.send_confirmation",
    JobType.SEND_CHANGE: "audit.job.send_change",
    JobType.SEND_REMINDER: "audit.job.send_reminder",
    JobType.SEND_CANCELLATION: "audit.job.send_cancellation",
    JobType.DISPATCH_WXCC_TASK: "audit.job.dispatch_wxcc_task",
    JobType.END_WXCC_TASK: "audit.job.end_wxcc_task",
    JobType.PURGE_PII: "audit.job.purge_pii",
    JobType.SEND_SUSPENSION: "audit.job.send_suspension",
    JobType.SEND_CLOSURE_UPDATE: "audit.job.send_closure_update",
    JobType.SEND_REINSTATEMENT: "audit.job.send_reinstatement",
    JobType.SEND_REBOOK_REQUEST: "audit.job.send_rebook_request",
    JobType.SEND_REBOOK_REMINDER: "audit.job.send_rebook_reminder",
    JobType.SEND_REBOOK_LAPSED: "audit.job.send_rebook_lapsed",
    JobType.LAPSE_REBOOK: "audit.job.lapse_rebook",
    JobType.SEND_CONFIRM_REQUEST: "audit.job.send_confirm_request",
    JobType.SEND_CONFIRM_REMINDER: "audit.job.send_confirm_reminder",
    JobType.EXPIRE_UNCONFIRMED: "audit.job.expire_unconfirmed",
    JobType.SEND_UNCONFIRMED_LAPSED: "audit.job.send_unconfirmed_lapsed",
    JobType.SEND_TEAM_INVITE: "audit.job.send_team_invite",
    JobType.SEND_TEAM_CANCEL: "audit.job.send_team_cancel",
}
"""Catalogue key per job type: the jobs list of the booking page (ADR 0068) and the audit log."""

JOB_STATUS_LABELS: dict[JobStatus, tuple[str, str]] = {
    JobStatus.PENDING: ("admin.booking.job.pending", ""),
    JobStatus.RUNNING: ("admin.booking.job.running", "accent"),
    JobStatus.DONE: ("admin.booking.job.done", "success"),
    JobStatus.FAILED: ("admin.booking.job.failed", "danger"),
}
"""Label key and chip tone per job status; the status is always written out (WCAG 1.4.1)."""


KIND_LABELS: dict[BookingKind, str] = {
    BookingKind.SCHEDULED: "call.kind.scheduled",
    BookingKind.DIRECT: "call.kind.direct",
}
