"""Storage of the last operator check results (``integration_reports``).

``webhooks-list``, ``webhooks-reset`` and ``webhooks-health`` run in the ``web`` or ``worker``
container against the shared database and store what they found here; the admin portal's
integrations page reads it. Reports are JSON objects that never contain secrets or tokens.
Writes take the write lock (``BEGIN IMMEDIATE``) like every other writer.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import IntegrationReportRow
from app.adapters.db.uow import locked_session

REGISTRATIONS_REPORT = "webhook_registrations"
HEALTH_REPORT = "webhook_health"


@dataclass(frozen=True)
class StoredReport:
    """A report and when the check that made it ran."""

    body: dict[str, Any]
    checked_at: datetime


class ReportStore:
    """Reads and writes check reports by name.

    Args:
        engine: The database engine.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Store the engine."""
        self._engine = engine

    async def get(self, name: str) -> StoredReport | None:
        """Return the report ``name``, or ``None`` if no check stored one yet."""
        async with AsyncSession(self._engine) as session:
            row = await session.get(IntegrationReportRow, name)
        if row is None:
            return None
        body = json.loads(row.body)
        return StoredReport(body if isinstance(body, dict) else {}, row.checked_at)

    async def put(self, name: str, body: dict[str, Any], now: datetime) -> None:
        """Create or replace the report ``name``."""
        async with locked_session(self._engine) as session:
            await session.merge(
                IntegrationReportRow(name=name, body=json.dumps(body), checked_at=now)
            )
