"""Several credentials read in one query (``CredentialStore.get_many``)."""

from pathlib import Path

import pytest

from app.adapters.crypto import SecretBox
from app.adapters.db.credentials import CredentialStore
from app.adapters.db.engine import create_engine
from tests.support.database import migrated_database
from tests.support.domain import NOW


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    return migrated_database(tmp_path, monkeypatch, "credentials-many")


async def test_the_credentials_that_exist_come_back_in_one_read(database_url: str) -> None:
    engine = create_engine(database_url)
    store = CredentialStore(engine, SecretBox(b"k" * 32))
    await store.put("a", {"n": 1}, NOW)
    await store.put("b", {"n": 2}, NOW)

    found = await store.get_many(["a", "b", "missing"])

    assert found == {"a": {"n": 1}, "b": {"n": 2}}
    assert await store.get_many([]) == {}
    await engine.dispose()
