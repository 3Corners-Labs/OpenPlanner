"""A configuration document service on a SQLite engine, for the tests of readers and writers.

The service reads the clock of ``CLOCK`` (a day after ``NOW``, so after every fixture that is
stamped with ``NOW``) and may do everything as the host actor; ``transfer`` moves a sealed export
from one installation to another under a different settings key.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.configuration_json import JsonCodec
from app.adapters.configuration_seal import PassphraseSealing
from app.adapters.crypto import SecretBox
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.configuration_changes import RuleContext
from app.domain.configuration_document import Scope
from app.domain.configuration_document_service import Actor, ConfigurationDocumentService
from tests.fakes.clock import FixedClock

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CLOCK = NOW + timedelta(days=1)
"""What the service takes for now: after every revision that the seed publishes."""
HOST = Actor.host()
PASSPHRASE = "a long enough passphrase"


def service(engine: AsyncEngine, key: bytes = b"a") -> ConfigurationDocumentService:
    """Return the document service on ``engine``, sealing under ``key * 32``."""
    clock = FixedClock(CLOCK)
    uow = SqlUnitOfWork(engine, SecretBox(key * 32))
    return ConfigurationDocumentService(
        uow,
        clock,
        AdminService(uow, clock),
        JsonCodec(),
        PassphraseSealing(),
        installation="test",
        app_version="0.0.0",
        context=lambda: RuleContext(),
        locked=lambda: frozenset(),
        max_bytes=lambda: 8 * 1024 * 1024,
        max_changes=lambda: 500,
        plaintext_export=lambda: True,
    )


async def export(engine: AsyncEngine) -> dict[str, Any]:
    """Return the whole installation as a document, without secrets."""
    exported: dict[str, Any] = json.loads(
        await service(engine).export(HOST, Scope(), None, secrets=False)
    )
    return exported


async def transfer(source: AsyncEngine, target: AsyncEngine) -> Any:
    """Export the source with its secrets sealed and apply it to the target (another key)."""
    sealed = await service(source).export(HOST, Scope(), PASSPHRASE, secrets=True)
    return await service(target, b"b").apply(HOST, sealed, PASSPHRASE, None)
