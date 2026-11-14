"""Encrypted storage of integration credentials (ADR 0008).

Values are JSON objects sealed with ``SecretBox`` under their name. Web and worker share them
through the database, so a token refreshed by one is used by the other. Writes take the write
lock (``BEGIN IMMEDIATE``) like every other writer.
"""

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import IntegrationCredentialRow
from app.adapters.db.uow import locked_session


class Secrets(Protocol):
    """The encrypted credential store, read and written by name (``CredentialStore``, a fake)."""

    async def get(self, name: str) -> dict[str, object] | None:
        """Return the credential ``name``, or ``None``."""
        ...

    async def get_many(self, names: Sequence[str]) -> dict[str, dict[str, object]]:
        """Return the credentials among ``names`` that exist, in one query."""
        ...

    async def put(self, name: str, value: dict[str, object], now: datetime) -> None:
        """Store the credential ``name``."""
        ...

    async def delete(self, name: str) -> None:
        """Remove the credential ``name``."""
        ...

    async def update(
        self,
        name: str,
        change: Callable[[dict[str, object] | None], dict[str, object] | None],
        now: datetime,
    ) -> bool:
        """Read ``name``, apply ``change`` to it and store the result, in one locked transaction.

        ``change`` gets the stored value (``None`` when there is none) and returns the value to
        store, or ``None`` to store nothing. Nothing can be written between the read and the
        write, so two changes of different fields of one record never overwrite each other.
        Returns whether something was stored.
        """
        ...


class CredentialStore:
    """Reads and writes encrypted credentials.

    Args:
        engine: The database engine.
        box: Seals and opens the values.
    """

    def __init__(self, engine: AsyncEngine, box: SecretBox) -> None:
        """Store the collaborators."""
        self._engine = engine
        self._box = box

    async def get(self, name: str) -> dict[str, Any] | None:
        """Return the credential ``name``, or ``None`` if none is stored."""
        async with AsyncSession(self._engine) as session:
            row = await session.get(IntegrationCredentialRow, name)
        if row is None:
            return None
        value: dict[str, Any] = json.loads(self._box.open(name, row.nonce, row.ciphertext))
        return value

    async def get_many(self, names: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Return the credentials among ``names`` that exist, read in one query."""
        if not names:
            return {}
        async with AsyncSession(self._engine) as session:
            rows = (
                await session.scalars(
                    select(IntegrationCredentialRow).where(IntegrationCredentialRow.name.in_(names))
                )
            ).all()
        return {
            row.name: json.loads(self._box.open(row.name, row.nonce, row.ciphertext))
            for row in rows
        }

    async def delete(self, name: str) -> None:
        """Remove the credential ``name``, if any."""
        async with locked_session(self._engine) as session:
            await session.execute(
                delete(IntegrationCredentialRow).where(IntegrationCredentialRow.name == name)
            )

    async def update(
        self,
        name: str,
        change: Callable[[dict[str, Any] | None], dict[str, Any] | None],
        now: datetime,
    ) -> bool:
        """Read ``name``, apply ``change`` and store the result in one ``BEGIN IMMEDIATE``."""
        async with locked_session(self._engine) as session:
            row = await session.get(IntegrationCredentialRow, name)
            current: dict[str, Any] | None = (
                json.loads(self._box.open(name, row.nonce, row.ciphertext)) if row else None
            )
            value = change(current)
            if value is None:
                return False
            nonce, ciphertext = self._box.seal(name, json.dumps(value).encode())
            await session.merge(
                IntegrationCredentialRow(
                    name=name, nonce=nonce, ciphertext=ciphertext, updated_at=now
                )
            )
            return True

    async def put(self, name: str, value: dict[str, Any], now: datetime) -> None:
        """Create or replace the credential ``name``."""
        nonce, ciphertext = self._box.seal(name, json.dumps(value).encode())
        async with locked_session(self._engine) as session:
            await session.merge(
                IntegrationCredentialRow(
                    name=name, nonce=nonce, ciphertext=ciphertext, updated_at=now
                )
            )
