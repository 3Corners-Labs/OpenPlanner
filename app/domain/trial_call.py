"""The trial call: a real Webex meeting the citizen opens ahead of the appointment (ADR 0070).

A first-time caller wants to know that camera, microphone, speakers and browser work, on the
same Webex site and with the same client as the appointment, not in a lookalike. So the trial
call is a meeting scheduled by the group's host exactly as a booking's meeting is
(``MeetingRequest``), with one difference: the citizen can enter alone, without the lobby, and
nobody is waiting for them. It opens when the citizen asks for it, closes after the group's
``trial_call_minutes`` and is deleted by a job. This module holds the rules; the service
(``app.domain.trial_call_service``) does the work.

    page ─► trial_state ─► AVAILABLE ─► create meeting ─► ACTIVE (link) ─► closes ─► deleted
                       └► ACTIVE (reuse the open one)    └► LIMIT_REACHED after N calls
"""

from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.booking import Booking
from app.domain.calls import MeetingRequest
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import Group

TRIAL_TITLE_PREFIX = "Proefgesprek"
"""Title prefix of a trial meeting, followed by the group's name (no citizen data)."""
CLEANUP_DELAY = timedelta(minutes=5)
"""The delete job runs this long after a trial call closes, so nobody is cut off."""


class TrialState(StrEnum):
    """What the trial call page can offer for a booking right now."""

    AVAILABLE = "available"
    ACTIVE = "active"
    LIMIT_REACHED = "limit_reached"
    TOO_LATE = "too_late"
    OFF = "off"
    INACTIVE = "inactive"


def trial_state(booking: Booking, group: Group, now: datetime) -> TrialState:
    """Return what a citizen can do with the trial call of ``booking`` at ``now``.

    Only a confirmed booking in a group with video and the trial call switched on gets one, and
    only before the appointment starts (from then on the real call is what counts).
    """
    if not group.trial_call_enabled or not group.video_enabled:
        return TrialState.OFF
    if not booking.is_active:
        return TrialState.INACTIVE
    if now >= booking.start:
        return TrialState.TOO_LATE
    if booking.trial is not None and now < booking.trial.expires_at:
        return TrialState.ACTIVE
    if booking.trial_count >= group.trial_calls_per_booking:
        return TrialState.LIMIT_REACHED
    return TrialState.AVAILABLE


def trial_request(group: Group, now: datetime) -> MeetingRequest:
    """Return the meeting to schedule for a trial call opened at ``now``.

    Starts at the minute ``now`` falls in, so Webex does not see a start in the past.

    Raises:
        DomainError: ``integration_not_ready`` (``video_host_email``) without a host.
    """
    if group.video_host_email is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "video_host_email")
    start = now.replace(second=0, microsecond=0)
    return MeetingRequest(
        title=f"{TRIAL_TITLE_PREFIX} {group.name}",
        start=start,
        end=start + timedelta(minutes=group.trial_call_minutes),
        timezone=group.timezone,
        host_email=group.video_host_email,
        join_before_host=True,
    )
