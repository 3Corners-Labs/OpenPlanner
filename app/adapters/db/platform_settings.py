"""SQL queries of the portal settings and their version counter (ADR 0042 §4, §6).

A mixin of ``SqlTransaction``. ``platform_settings`` holds one JSON value per class C
setting, with an expiry for ``LOG_LEVEL``; ``organisation_settings`` one per organisation and
class D setting; ``settings_version`` holds at most one row, and
without it the version is 0. Class C secrets are sealed in ``integration_credentials`` under
``setting:<key>`` (ADR 0008) and opened only here; without a settings key none is read. A value
that no longer parses (a hand-edited row) is left out, so the default or the environment
applies; the registry's rules are checked by the domain, never here.
"""

import json
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import (
    IntegrationCredentialRow,
    OrganisationSettingRow,
    PlatformSettingRow,
    SettingsVersionRow,
)

_VERSION_ID = 1
_SECRET_PREFIX = "setting:"  # noqa: S105  # nosec B105 - a name prefix, not a secret


class PlatformSettingsQueries:
    """The portal settings' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession
    _box: SecretBox | None

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

    async def organisation_settings(self) -> dict[int, dict[str, object]]:
        """Return every organisation's stored class D values that parse as JSON, by id."""
        rows = await self._session.scalars(select(OrganisationSettingRow))
        values: dict[int, dict[str, object]] = {}
        for row in rows:
            try:
                values.setdefault(row.organisation_id, {})[row.key] = json.loads(row.value)
            except ValueError:
                continue
        return values

    async def save_organisation_setting(
        self, organisation_id: int, key: str, value: object, updated_at: datetime
    ) -> None:
        """Store or replace one organisation's value of ``key`` as JSON."""
        await self._session.merge(
            OrganisationSettingRow(
                organisation_id=organisation_id,
                key=key,
                value=json.dumps(value),
                updated_at=updated_at,
            )
        )
        await self._session.flush()

    async def delete_organisation_setting(self, organisation_id: int, key: str) -> bool:
        """Remove one organisation's value of ``key``; return whether there was one."""
        result = await self._session.execute(
            delete(OrganisationSettingRow).where(
                OrganisationSettingRow.organisation_id == organisation_id,
                OrganisationSettingRow.key == key,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def platform_secrets(self) -> dict[str, str]:
        """Return every sealed setting secret, opened; empty without a settings key."""
        if self._box is None:
            return {}
        rows = await self._session.scalars(
            select(IntegrationCredentialRow).where(
                IntegrationCredentialRow.name.startswith(_SECRET_PREFIX)
            )
        )
        found: dict[str, str] = {}
        for row in rows:
            try:
                opened = json.loads(self._box.open(row.name, row.nonce, row.ciphertext))
            except Exception:  # noqa: S112 - another key or a broken row: leave it out
                continue
            if isinstance(opened, dict) and isinstance(opened.get("value"), str):
                found[row.name.removeprefix(_SECRET_PREFIX)] = opened["value"]
        return found

    async def secrets_available(self) -> bool:
        """Return whether a settings key is configured, so secrets can be sealed."""
        return self._box is not None

    async def save_platform_secret(self, key: str, value: str, updated_at: datetime) -> None:
        """Seal ``value`` under ``setting:<key>`` and store it.

        Raises:
            RuntimeError: Without a settings key (the service checks this first).
        """
        if self._box is None:
            raise RuntimeError("no settings key")
        name = _SECRET_PREFIX + key
        nonce, ciphertext = self._box.seal(name, json.dumps({"value": value}).encode())
        await self._session.merge(
            IntegrationCredentialRow(
                name=name, nonce=nonce, ciphertext=ciphertext, updated_at=updated_at
            )
        )
        await self._session.flush()

    async def delete_platform_secret(self, key: str) -> bool:
        """Remove the sealed value of ``key``; return whether there was one."""
        result = await self._session.execute(
            delete(IntegrationCredentialRow).where(
                IntegrationCredentialRow.name == _SECRET_PREFIX + key
            )
        )
        return bool(getattr(result, "rowcount", 0))
