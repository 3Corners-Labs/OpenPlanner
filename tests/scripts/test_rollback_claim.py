"""The claim the upgrade matrix lets the previous image make (ADR 0121 A30, gate item G43)."""

import asyncio
import json
from pathlib import Path

import pytest

from app.adapters.db.engine import create_engine
from app.adapters.db.gated_records import GatedRecords, write_gated_records
from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import Booking
from scripts.rollback_claim import claim_all, claimed_by, main
from tests.support.database import migrated_database
from tests.support.domain import NOW, TEST_TOKEN_KEY


async def _written(url: str) -> tuple[GatedRecords, Booking]:
    """Write the gated records with the current code; return them and the started booking."""
    engine = create_engine(url)
    try:
        await seed_demo(engine)
        records = await write_gated_records(engine, NOW, TEST_TOKEN_KEY)
        async with SqlUnitOfWork(engine).read() as tx:
            started = await tx.get_booking(records.pool_booking)
    finally:
        await engine.dispose()
    assert started is not None
    return records, started


def test_a_booking_is_claimed_once_and_work_already_held_is_never_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = migrated_database(tmp_path, monkeypatch)
    records, started = asyncio.run(_written(url))
    assert started.claim is not None  # the current code's claim of its work
    holder = started.claim.claimed_by
    free = records.legacy_booking  # confirmed, unclaimed, as the previous image writes it

    found = asyncio.run(claim_all(url, "previous-image", [free, free, records.pool_booking]))

    assert found == [True, False, False]  # once, never twice, never work already held
    assert asyncio.run(claimed_by(url, [free, records.pool_booking])) == {
        free: "previous-image",
        records.pool_booking: holder,
    }


def test_the_script_prints_one_line_of_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = migrated_database(tmp_path, monkeypatch)

    assert main(["claimed", url, "999999"]) == 0

    assert json.loads(capsys.readouterr().out) == {"999999": None}
