"""What the stored check reports say, read in one place (``integration_reports``).

``app.adapters.db.reports`` stores and returns whole reports; a finding that other parts of the
application act on is read from here, so the settings form and the configuration package's
snapshot can never read the same fact two ways. A module of its own keeps the persistence layer
free of an import loop (the unit of work reads the snapshot, the store needs the unit of work).
"""

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import IntegrationReportRow
from app.domain.guest_access import guest_check_passed
from app.domain.integration_check import SERVICE_APP_REPORT


def report_object(raw: str) -> dict[str, Any]:
    """Return a stored report's text as an object; anything else reads as an empty report."""
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def guest_token_minted(session: AsyncSession) -> bool:
    """Return whether the latest service-app check minted a guest token (ADR 0123 point 17).

    The one reader of that fact, on the caller's session: the group settings form
    (``ReportStore.guest_check_passed``) and the configuration package's snapshot both ask here.
    """
    row = await session.get(IntegrationReportRow, SERVICE_APP_REPORT)
    return guest_check_passed(report_object(row.body) if row else None)
