"""The check behind the watch on the audit events the tests make (ADR 0103, ADR 0044).

The session fixture ``_audited_actions_are_registered`` of ``tests/conftest.py`` runs it on every
event the code in ``app/`` builds; ``tests/domain/test_activity_alert.py`` tests it on its own.
"""

from types import FrameType

from app.domain.activity_alert import COUNTED_ACTIONS
from app.domain.admin import AdminService
from app.domain.audit import AuditEvent, Outcome

RECORDER = AdminService.record_change.__code__
"""The one method that may write a success an hourly alert counts: it runs the alert."""


def checked_by_its_alert(event: AuditEvent, frame: FrameType | None) -> bool:
    """Whether ``event`` is no counted success, or is built under ``AdminService.record_change``.

    That method runs the hourly alert right after the event (``app.domain.activity_alert``); a
    counted success built anywhere else would let a burst pass its threshold unseen. ``frame``
    is the frame that built the event; the walk goes up the stack from there.
    """
    if event.outcome is not Outcome.SUCCESS or event.action not in COUNTED_ACTIONS:
        return True
    while frame is not None:
        if frame.f_code is RECORDER:
            return True
        frame = frame.f_back
    return False
