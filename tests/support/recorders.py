"""A stand-in with the qualified name of the one method that records counted successes.

The audit watch of ``tests/conftest.py`` accepts a success an hourly alert counts only when
``AdminService.record_change`` is on the stack; this class gives its test that frame without the
real service.
"""

import sys
from types import FrameType


class AdminService:
    """Named like ``app.domain.admin.AdminService``, for the watch's test only."""

    def record_change(self) -> FrameType:
        """Return this method's frame, as the real method's frame stands on the stack."""
        return sys._getframe()
