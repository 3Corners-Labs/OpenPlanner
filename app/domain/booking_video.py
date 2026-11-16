"""Whether a booking is a video call and gets a meeting (ADR 0121 row 36).

The one question every place asks that makes, links or shows a booking's meeting: the worker's
``create_video_room``, a planner's retry, the message's join link, the participant's join and
trial call, and the queue. A booking is a video call when its flow is (``has_video``: no flow, a
legacy flow or a video flow); it gets a meeting when, in addition, its service has a video host
(``Group.video_enabled``). A booking of a new flow without video gets neither (A19).
"""

from app.domain.booking import Booking
from app.domain.ports import Transaction
from app.domain.scheduling import Group
from app.domain.service_flows import has_video


async def flow_has_video(tx: Transaction, booking: Booking) -> bool:
    """Return whether ``booking`` is a video call by its flow (``has_video``)."""
    if booking.flow is None:
        return True
    return has_video(await tx.flow_revision(booking.flow.flow_id, booking.flow.revision), True)


async def gets_meeting(tx: Transaction, booking: Booking, group: Group) -> bool:
    """Return whether ``booking`` gets a meeting: its service has a host and it is a video call."""
    return group.video_enabled and await flow_has_video(tx, booking)
