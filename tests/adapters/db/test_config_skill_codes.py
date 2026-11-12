"""The contact-center code of a skill travels in the configuration document (ADR 0121 4b, A29).

On SQLite: an export carries the code of a mapped skill and none for an unmapped one, an import
writes it, importing the same document again changes nothing, and a change of the labels in a
document that does not name the code keeps the stored one.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.engine import create_engine
from app.adapters.db.models import OrganisationRow, SkillRow
from app.domain.configuration_document import Scope
from tests.support.config_seed import seed
from tests.support.config_service import HOST, export, service, transfer
from tests.support.database import migrated_database
from tests.support.flow_documents import encode

LABELS = json.dumps({"nl": "Duits", "en": "German"})


@pytest.fixture
async def source(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, busy_timeout_ms=200)
    await seed(engine)
    async with AsyncSession(engine) as session, session.begin():
        acme = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == "acme"))
        assert acme is not None
        session.add_all(
            [
                SkillRow(
                    organisation_id=acme.id,
                    code="german",
                    labels=LABELS,
                    contact_center_code="lang_de",
                ),
                SkillRow(organisation_id=acme.id, code="intake", labels=LABELS),
            ]
        )
    yield engine
    await engine.dispose()


@pytest.fixture
async def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database(tmp_path, monkeypatch, "target"), busy_timeout_ms=200)
    yield engine
    await engine.dispose()


async def _codes(engine: AsyncEngine) -> dict[str, str | None]:
    async with AsyncSession(engine) as session:
        rows = (await session.execute(select(SkillRow.code, SkillRow.contact_center_code))).all()
    return dict(rows)


async def test_the_code_goes_out_and_comes_back(source: AsyncEngine, target: AsyncEngine) -> None:
    exported = await export(source)
    skills = {s["code"]: s for s in exported["organisations"][0]["skills"]}

    applied = await transfer(source, target)
    again = await service(target, b"b").plan(
        HOST, await service(target, b"b").export(HOST, Scope(), None, secrets=False), None
    )

    assert skills["german"]["contact_center_code"] == "lang_de"
    assert "contact_center_code" not in skills["intake"]
    assert applied.applied, applied.plan.refusals
    assert await _codes(target) == {"german": "lang_de", "intake": None}
    assert again.changes == ()


async def test_a_change_of_the_labels_keeps_the_stored_code(source: AsyncEngine) -> None:
    exported = await export(source)
    german = next(s for s in exported["organisations"][0]["skills"] if s["code"] == "german")
    german["labels"] = {"nl": "Duitse taal", "en": "German language"}
    del german["contact_center_code"]

    await service(source).apply(HOST, encode(exported), None, None)

    assert (await _codes(source))["german"] == "lang_de"
