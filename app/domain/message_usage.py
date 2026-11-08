"""The usage facts of the messages sent to participants (ADR 0117 step 3, metric M1).

The job runner calls ``record_message`` after a message job has sent its email or SMS and when
it has given up on one. The fact says which channel and for what purpose, never to whom, what
the text was or what the sender answered (a provider's error text can hold an address):

    sent      ─► ``message.sent``   (channel kind, purpose)
    gave up   ─► ``message.failed`` (channel kind, purpose); a retry that later works is one
                 ``message.sent`` and no failure

The purpose is a code of the closed list ``PURPOSES``, one per kind of message job. The team's
own calendar invite goes to a mailbox of the organisation, not to a participant: no fact.
Facts are written in a transaction of their own, because the send is not part of one; a failure
to write is the caller's to log, and never makes a sent message look unsent.
"""

from datetime import datetime

from app.domain.booking_facts import keyed
from app.domain.channels import ChannelKind
from app.domain.job_plan import JobType
from app.domain.ports import UnitOfWork
from app.domain.usage import ActorClass, FactKind
from app.domain.usage_recording import facts_of_booking

PURPOSES: dict[JobType, str] = {
    JobType.SEND_CONFIRMATION: "confirmation",
    JobType.SEND_CHANGE: "change",
    JobType.SEND_REMINDER: "reminder",
    JobType.SEND_CANCELLATION: "cancellation",
    JobType.SEND_SUSPENSION: "closure",
    JobType.SEND_CLOSURE_UPDATE: "closure",
    JobType.SEND_REINSTATEMENT: "closure",
    JobType.SEND_REBOOK_REQUEST: "rebook",
    JobType.SEND_REBOOK_REMINDER: "rebook",
    JobType.SEND_REBOOK_LAPSED: "rebook",
    JobType.SEND_CONFIRM_REQUEST: "confirm_request",
    JobType.SEND_CONFIRM_REMINDER: "confirm_request",
    JobType.SEND_UNCONFIRMED_LAPSED: "confirm_request",
}
"""The purpose of each message job, the second cut of metric M1 (a closed list)."""


async def record_message(
    uow: UnitOfWork,
    job_type: JobType,
    channel: ChannelKind,
    booking_id: int,
    at: datetime,
    *,
    sent: bool,
) -> None:
    """Write ``message.sent`` (or ``message.failed``) for a message job of ``booking_id``.

    A job type that is not a message to a participant, or a booking or group that is gone,
    makes no fact.
    """
    purpose = PURPOSES.get(job_type)
    if purpose is None:
        return
    async with uow.locked() as tx:
        booking = await tx.get_booking(booking_id)
        group = await tx.get_group_by_id(booking.group_id) if booking else None
        if booking is None or group is None:
            return
        kind = FactKind.MESSAGE_SENT if sent else FactKind.MESSAGE_FAILED
        await tx.record_usage(
            keyed(
                kind,
                facts_of_booking(booking, group),
                at,
                channel_kind=channel,
                reason_code=purpose,
                actor_class=ActorClass.SYSTEM,
            )
        )
