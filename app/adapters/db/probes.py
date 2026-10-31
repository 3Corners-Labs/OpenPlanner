"""``ProbeRegistry`` on the ``integration_reports`` table (ADR 0082 point 2).

The group check creates a probe work item and waits for its ``task:new`` and ``task:ended``
events; the webhook handler runs in the same ``web`` container but in another request, so
the two meet in the database. One report row, ``wxcc_probe``, holds the open probe's task id
and the events noted for it: a new probe replaces the previous one, so two checks at once
would see only the later probe (the check button is rate-limited, and a replaced probe is at
worst reported as one without events). No task data is kept, only the id and event kinds.
"""

from datetime import datetime

from app.adapters.db.reports import ReportStore
from app.domain.calls import EventKind

PROBE_REPORT = "wxcc_probe"


class ProbeReports:
    """Keeps the open probe work item and its events in ``integration_reports``.

    Args:
        reports: The report store of the shared database.
    """

    def __init__(self, reports: ReportStore) -> None:
        """Store the report store."""
        self._reports = reports

    async def open(self, task_id: str, at: datetime) -> None:
        """Start waiting for ``task_id``; an earlier probe is forgotten."""
        await self._reports.put(PROBE_REPORT, {"task_id": task_id, "events": []}, at)

    async def note(self, task_id: str, kind: EventKind, at: datetime) -> bool:
        """Append ``kind`` when ``task_id`` is the open probe; return whether it was."""
        report = await self._reports.get(PROBE_REPORT)
        if report is None or report.body.get("task_id") != task_id:
            return False
        events = [str(e) for e in report.body.get("events", []) if isinstance(e, str)]
        events.append(kind.value)
        await self._reports.put(PROBE_REPORT, {"task_id": task_id, "events": events}, at)
        return True

    async def events(self, task_id: str) -> tuple[EventKind, ...]:
        """Return the events noted for ``task_id``, in order of arrival."""
        report = await self._reports.get(PROBE_REPORT)
        if report is None or report.body.get("task_id") != task_id:
            return ()
        noted = []
        for value in report.body.get("events", []):
            try:
                noted.append(EventKind(str(value)))
            except ValueError:
                continue  # an event kind of a newer version
        return tuple(noted)
