from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.models import IntegrationCredentialRow
from tests.support.domain import NOW


async def test_credentials_are_stored_encrypted_and_replaced(engine: AsyncEngine) -> None:
    store = CredentialStore(engine, SecretBox(b"k" * 32))

    assert await store.get("webex_service_app") is None
    await store.put("webex_service_app", {"access_token": "first"}, NOW)
    await store.put("webex_service_app", {"access_token": "second"}, NOW)

    assert await store.get("webex_service_app") == {"access_token": "second"}
    async with AsyncSession(engine) as session:
        rows = list(await session.scalars(select(IntegrationCredentialRow)))
    assert len(rows) == 1
    assert b"second" not in rows[0].ciphertext
