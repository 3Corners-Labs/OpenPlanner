"""SQL queries of the portal settings and their version counter (ADR 0042 §4, §6).

A mixin of ``SqlTransaction``. ``platform_settings`` holds one JSON value per class C
setting, with an expiry for ``LOG_LEVEL``; ``settings_version`` holds at most one row, and
without it the version is 0. A value
that no longer parses (a hand-edited row) is left out, so the default or the environment
applies; the registry's rules are checked by the domain, never here.
"""

import json
from datetime import datetime

from sqlalchemy import delete, select
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

    async def platform_setting_expiries(self) -> dict[str, datetime]:
        """Return the expiry of every stored value that has one."""
        rows = await self._session.execute(
            select(PlatformSettingRow.key, PlatformSettingRow.expires_at).where(
                PlatformSettingRow.expires_at.is_not(None)
            )
        )
        return {key: at for key, at in rows if at is not None}

    async def save_platform_setting(
        self, key: str, value: object, updated_at: datetime, expires_at: datetime | None
    ) -> None:
        """Store or replace the value of ``key`` as JSON."""
        await self._session.merge(
            PlatformSettingRow(
                key=key, value=json.dumps(value), updated_at=updated_at, expires_at=expires_at
            )
        )
        await self._session.flush()

    async def delete_platform_setting(self, key: str) -> bool:
        """Remove the value of ``key``; return whether there was one."""
        result = await self._session.execute(
            delete(PlatformSettingRow).where(PlatformSettingRow.key == key)
        )
        return bool(getattr(result, "rowcount", 0))

    async def bump_settings_version(self) -> int:
        """Raise the counter by one, creating its row on the first change."""
        row = await self._session.get(SettingsVersionRow, _VERSION_ID)
        if row is None:
            row = SettingsVersionRow(id=_VERSION_ID, version=0)
            self._session.add(row)
        row.version += 1
        await self._session.flush()
        return row.version
