"""When the application first saw each installation webhook secret (ADR 0124 point 8).

The installation's own webhook secrets come from the environment, which the application cannot
write, so the moment a secret was rotated is the moment the application first saw it. That moment
and a keyed fingerprint of the secret (``app.domain.webhook_secrets.fingerprint``: an HMAC that
cannot be turned back into the secret) are kept in the existing key-value store of check reports
(``integration_reports``), one row per platform. No new table, so no migration. The row holds no
secret and no previous secret.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.reports import ReportStore
from app.domain.webhook_secrets import Sighting

NAME_PREFIX = "webhook_secret_seen:"
"""The report name of a platform's sighting: the prefix and the platform's code."""


class StoredSightings:
    """``SecretSightings`` on the report store.

    Args:
        engine: The database engine.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Store the report store over ``engine``."""
        self._reports = ReportStore(engine)

    async def get(self, platform: str) -> Sighting | None:
        """Return the stored sighting of ``platform``, or ``None`` (none, or not readable)."""
        report = await self._reports.get(f"{NAME_PREFIX}{platform}")
        if report is None:
            return None
        made, since = report.body.get("fingerprint"), report.body.get("since")
        if not isinstance(made, str) or not isinstance(since, str):
            return None
        try:
            return Sighting(made, datetime.fromisoformat(since))
        except ValueError:
            return None

    async def put(self, platform: str, sighting: Sighting) -> None:
        """Store the sighting of ``platform``."""
        await self._reports.put(
            f"{NAME_PREFIX}{platform}",
            {"fingerprint": sighting.fingerprint, "since": sighting.since.isoformat()},
            sighting.since,
        )
