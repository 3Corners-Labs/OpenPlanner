"""SQL queries of the public address's identity and round trip (ADR 0086).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction. ``instance``
holds one row: the installation's id and the hash of the one pending probe nonce.
``public_address_checks`` holds at most one row per address. A stored finding or forwarded
state that no longer parses (a hand-edited row) is read as ``unreachable`` or ``None`` rather
than failing the status page.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import InstanceRow, PublicAddressCheckRow
from app.domain.public_address import AddressCheck, AddressFinding, AddressKind, Forwarded

_INSTANCE_ID = 1


def _finding(raw: str) -> AddressFinding:
    """Parse a stored finding; anything unknown counts as unreachable."""
    try:
        return AddressFinding(raw)
    except ValueError:
        return AddressFinding.UNREACHABLE


def _forwarded(raw: str | None) -> Forwarded | None:
    """Parse a stored forwarded state; anything unknown counts as none."""
    try:
        return Forwarded(raw) if raw else None
    except ValueError:
        return None


class PublicAddressQueries:
    """The public address's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_instance_id(self) -> str | None:
        """Return the installation's id, or ``None`` before one was made."""
        row = await self._session.get(InstanceRow, _INSTANCE_ID)
        return row.instance_id if row else None

    async def ensure_instance_id(self) -> str:
        """Return the installation's id, making and storing it on first use."""
        row = await self._session.get(InstanceRow, _INSTANCE_ID)
        if row is None:
            row = InstanceRow(id=_INSTANCE_ID, instance_id=str(uuid.uuid4()))
            self._session.add(row)
            await self._session.flush()
        return row.instance_id

    async def store_probe_nonce(self, digest: str, at: datetime) -> None:
        """Keep the hash of the one pending nonce, replacing any earlier one."""
        await self.ensure_instance_id()
        await self._session.execute(
            update(InstanceRow)
            .where(InstanceRow.id == _INSTANCE_ID)
            .values(probe_nonce_hash=digest, probe_nonce_at=at)
        )

    async def take_probe_nonce(self, digest: str, not_before: datetime) -> bool:
        """Consume the pending nonce when it matches and is newer than ``not_before``."""
        result = await self._session.execute(
            update(InstanceRow)
            .where(
                InstanceRow.id == _INSTANCE_ID,
                InstanceRow.probe_nonce_hash == digest,
                InstanceRow.probe_nonce_at >= not_before,
            )
            .values(probe_nonce_hash=None, probe_nonce_at=None)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def get_address_checks(self) -> list[AddressCheck]:
        """Return the latest round trip per address."""
        rows = (await self._session.scalars(select(PublicAddressCheckRow))).all()
        checks = []
        for row in rows:
            try:
                kind = AddressKind(row.kind)
            except ValueError:
                continue
            checks.append(
                AddressCheck(
                    kind,
                    row.checked_at,
                    row.url,
                    _finding(row.finding),
                    row.scheme,
                    _forwarded(row.forwarded),
                    row.error,
                )
            )
        return checks

    async def save_address_check(self, check: AddressCheck) -> None:
        """Replace the stored round trip of ``check.kind``."""
        await self._session.merge(
            PublicAddressCheckRow(
                kind=check.kind.value,
                checked_at=check.checked_at,
                url=check.url[:255],
                finding=check.finding.value,
                scheme=check.scheme,
                forwarded=check.forwarded.value if check.forwarded else None,
                error=check.error,
            )
        )
        await self._session.flush()

    async def delete_address_check(self, kind: AddressKind) -> None:
        """Forget the stored round trip of ``kind``."""
        await self._session.execute(
            delete(PublicAddressCheckRow).where(PublicAddressCheckRow.kind == kind.value)
        )
