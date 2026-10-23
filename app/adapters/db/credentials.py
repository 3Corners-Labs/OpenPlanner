"""Encrypted storage of integration credentials (ADR 0008).

Values are JSON objects sealed with ``SecretBox`` under their name. Web and worker share them
through the database, so a token refreshed by one is used by the other. Writes take the write
lock (``BEGIN IMMEDIATE``) like every other writer.
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import IntegrationCredentialRow
from app.adapters.db.uow import locked_session


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

    async def put(self, name: str, value: dict[str, Any], now: datetime) -> None:
        """Create or replace the credential ``name``."""
        nonce, ciphertext = self._box.seal(name, json.dumps(value).encode())
        async with locked_session(self._engine) as session:
            await session.merge(
                IntegrationCredentialRow(
                    name=name, nonce=nonce, ciphertext=ciphertext, updated_at=now
                )
            )
