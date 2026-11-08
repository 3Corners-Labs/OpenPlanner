"""SQL queries of connection health and task reconciliation (ADR 0082 points 5 to 7).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. The health of
every connection and each platform's last inbound webhook are stored as integration reports
(``integration-health``, ``inbound-<platform>``; no new table, ADR 0082 *Consequences*). The
samples come from the system events ``integration.request`` (codes only, never a body); the
stale tasks from ``call_status`` and the booking's task id. Nothing here reads personal data.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.contact_center_rows import has_any_task, stored_task_id, stored_task_kind
from app.adapters.db.models import (
    AuditEventRow,
    BookingRow,
    CallStatusRow,
    GroupRow,
    IntegrationReportRow,
    OrganisationRow,
    SlotRow,
)
from app.domain.audit import Outcome
from app.domain.calls import TaskState
from app.domain.connection_health import (
    WATCHED_TASKS,
    HealthEntry,
    HealthState,
    InboundEvent,
    Platform,
    RequestSample,
    StaleTask,
)
from app.domain.contact_center import ContactCenterKind
from app.domain.integration_check import CHECK_EVENT, PROBE_REASON_PREFIX
from app.domain.request_log import REQUEST_EVENT, counts_as_failure, request_platform
from app.domain.security_log import TOKEN_REFRESH_FAILED

HEALTH_NAME = "integration-health"
"""The integration report that holds the health of every connection."""


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession


def _inbound_name(platform: Platform) -> str:
    """Return the report name of a platform's last inbound webhook."""
    return f"inbound-{platform.value}"


def _entry(item: dict[str, Any]) -> HealthEntry | None:
    """Read one stored entry; ``None`` when it no longer reads (a newer or older release)."""
    try:
        return HealthEntry(
            item.get("organisation") or None,
            Platform(item["platform"]),
            HealthState(item["state"]),
            datetime.fromisoformat(item["since"]),
            int(item.get("requests", 0)),
            int(item.get("failures", 0)),
            str(item.get("reason", "")),
        )
    except (KeyError, ValueError, TypeError):
        return None


class HealthQueries:
    """Reads the samples and stale tasks, and stores the health and the inbound webhooks."""

    async def request_samples(self: _Transaction, since: datetime) -> list[RequestSample]:
        """Return the request events since ``since`` that name a platform."""
        rows = await self._session.execute(
            select(
                AuditEventRow.organisation,
                AuditEventRow.detail,
                AuditEventRow.outcome,
                AuditEventRow.reason,
                AuditEventRow.at,
            ).where(AuditEventRow.action == REQUEST_EVENT, AuditEventRow.at >= since)
        )
        samples: list[RequestSample] = []
        for organisation, detail, outcome, reason, at in rows:
            platform = request_platform(detail)
            if platform is not None:
                failed = counts_as_failure(outcome == Outcome.FAILURE.value, reason, detail)
                samples.append(RequestSample(organisation, platform, failed, at))
        return samples

    async def token_refresh_failed_since(self: _Transaction, since: datetime) -> bool:
        """Return whether a token refresh failed since ``since``."""
        found = await self._session.scalar(
            select(func.count(AuditEventRow.id)).where(
                AuditEventRow.action == TOKEN_REFRESH_FAILED,
                AuditEventRow.outcome == Outcome.FAILURE.value,
                AuditEventRow.at >= since,
            )
        )
        return bool(found)

    async def failed_probe_organisations(self: _Transaction, since: datetime) -> frozenset[str]:
        """Return the organisations with a group whose latest check since ``since`` failed.

        Only a failure on the probe work item counts; a later passing check clears the group.
        """
        latest = (
            select(func.max(AuditEventRow.id).label("last"))
            .where(AuditEventRow.action == CHECK_EVENT, AuditEventRow.at >= since)
            .group_by(AuditEventRow.organisation, AuditEventRow.group)
            .subquery()
        )
        rows = await self._session.scalars(
            select(AuditEventRow.organisation)
            .join(latest, AuditEventRow.id == latest.c.last)
            .where(
                AuditEventRow.outcome == Outcome.FAILURE.value,
                AuditEventRow.reason.startswith(PROBE_REASON_PREFIX),
            )
        )
        return frozenset(organisation for organisation in rows if organisation)

    async def get_health(self: _Transaction) -> tuple[HealthEntry, ...]:
        """Return the stored health of every connection."""
        row = await self._session.get(IntegrationReportRow, HEALTH_NAME)
        if row is None:
            return ()
        body = json.loads(row.body)
        items = body.get("entries", []) if isinstance(body, dict) else []
        return tuple(e for e in (_entry(i) for i in items if isinstance(i, dict)) if e)

    async def save_health(self: _Transaction, entries: Sequence[HealthEntry], at: datetime) -> None:
        """Replace the stored health of every connection."""
        body = {
            "entries": [
                {
                    "organisation": e.organisation,
                    "platform": e.platform.value,
                    "state": e.state.value,
                    "since": e.since.isoformat(),
                    "requests": e.requests,
                    "failures": e.failures,
                    "reason": e.reason,
                }
                for e in entries
            ]
        }
        await self._session.merge(
            IntegrationReportRow(name=HEALTH_NAME, body=json.dumps(body), checked_at=at)
        )

    async def save_inbound_event(self: _Transaction, event: InboundEvent) -> None:
        """Store ``event`` as its platform's latest verified webhook."""
        body = {"kind": event.kind[:64], "outcome": event.outcome[:32]}
        await self._session.merge(
            IntegrationReportRow(
                name=_inbound_name(event.platform), body=json.dumps(body), checked_at=event.at
            )
        )

    async def get_inbound_events(self: _Transaction) -> tuple[InboundEvent, ...]:
        """Return each platform's latest verified webhook, in platform order."""
        found: list[InboundEvent] = []
        for platform in Platform:
            row = await self._session.get(IntegrationReportRow, _inbound_name(platform))
            if row is None:
                continue
            body = json.loads(row.body)
            body = body if isinstance(body, dict) else {}
            found.append(
                InboundEvent(
                    platform,
                    row.checked_at,
                    str(body.get("kind", "")),
                    str(body.get("outcome", "")),
                )
            )
        return tuple(found)

    async def stale_tasks(
        self: _Transaction, unchanged_before: datetime, limit: int
    ) -> list[StaleTask]:
        """Return the tasks still queued or connected that have not changed since then."""
        changed = func.coalesce(CallStatusRow.connected_at, CallStatusRow.task_created_at)
        rows = await self._session.execute(
            select(
                BookingRow.id,
                stored_task_id(),
                stored_task_kind(),
                CallStatusRow.task,
                OrganisationRow.slug,
                GroupRow.slug,
            )
            .join(CallStatusRow, CallStatusRow.booking_id == BookingRow.id)
            .join(SlotRow, SlotRow.id == BookingRow.slot_id)
            .join(GroupRow, GroupRow.id == BookingRow.group_id)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .where(
                has_any_task(),
                SlotRow.start < unchanged_before,
                CallStatusRow.task.in_([s.value for s in WATCHED_TASKS]),
                or_(changed.is_(None), changed < unchanged_before),
            )
            .order_by(changed, BookingRow.id)
            .limit(limit)
        )
        return [
            StaleTask(
                booking_id,
                task_id or "",
                TaskState(task),
                organisation,
                group,
                ContactCenterKind(kind),
            )
            for booking_id, task_id, kind, task, organisation, group in rows
        ]
