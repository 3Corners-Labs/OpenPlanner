"""SQL queries of the portal settings and their version counter (ADR 0042 §4, §6).

A mixin of ``SqlTransaction``. ``platform_settings`` holds one JSON value per class C
setting; ``settings_version`` holds at most one row, and without it the version is 0. A value
that no longer parses (a hand-edited row) is left out, so the default or the environment
applies; the registry's rules are checked by the domain, never here.
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import PlatformSettingRow, SettingsVersionRow

_VERSION_ID = 1


class PlatformSettingsQueries:
    """The portal settings' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def settings_version(self) -> int:
        """Return the settings counter; 0 when nothing was ever changed."""
        row = await self._session.get(SettingsVersionRow, _VERSION_ID)
        return row.version if row is not None else 0

    async def platform_settings(self) -> dict[str, object]:
        """Return every stored portal value that parses as JSON, by key."""
        rows = await self._session.scalars(select(PlatformSettingRow))
        values: dict[str, object] = {}
        for row in rows:
            try:
                values[row.key] = json.loads(row.value)
            except ValueError:
                continue
        return values
