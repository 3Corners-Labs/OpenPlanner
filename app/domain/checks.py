"""Findings of the "Koppeling controleren" check of a group (verify button).

A ``Finding`` is one link of a group's integration chain: the Webex host, the WxCC routing
(channel, asset, schema, entry point, flow, queue, Screen Pop), the webhooks or the worker
that runs the group's jobs (ADR 0068). It carries a
stable code and the names the text needs; the admin portal turns the code into a translated
sentence. Findings never hold secrets or personal data of citizens.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CheckArea(StrEnum):
    """Which part of the chain a finding is about."""

    PLATFORM = "platform"
    WEBEX = "webex"
    WXCC = "wxcc"
    WEBHOOKS = "webhooks"
    WORKER = "worker"
    """The worker process and the group's job queue (ADR 0068)."""


class CheckStatus(StrEnum):
    """Outcome of one finding, from best to worst (``SKIPPED``: nothing to check)."""

    OK = "ok"
    SKIPPED = "skipped"
    WARN = "warn"
    FAIL = "fail"


_SEVERITY = {CheckStatus.SKIPPED: 0, CheckStatus.OK: 1, CheckStatus.WARN: 2, CheckStatus.FAIL: 3}


@dataclass(frozen=True)
class Finding:
    """One checked link.

    Attributes:
        area: Webex, WxCC routing or webhooks.
        code: Stable code such as ``wxcc.channel.missing``; the text is chosen where it is
            shown.
        status: OK, warning, failure or skipped.
        params: Names the text needs (channel, asset, queue), as ``(key, value)`` pairs.
    """

    area: CheckArea
    code: str
    status: CheckStatus
    params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GroupCheck:
    """The latest check of one group."""

    group_id: int
    checked_at: datetime
    findings: tuple[Finding, ...]

    @property
    def overall(self) -> CheckStatus:
        """The worst status among the findings; skipped when nothing could be checked."""
        statuses = [f.status for f in self.findings] or [CheckStatus.SKIPPED]
        return max(statuses, key=_SEVERITY.__getitem__)
