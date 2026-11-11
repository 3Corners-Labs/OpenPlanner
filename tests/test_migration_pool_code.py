"""Migration 0110 (ADR 0121 step 2f): the code of a counted capacity pool and its backfill.

Counted pools get a code derived from their name, unique within the organisation and decided in
id order; legacy pools keep NULL; downgrading drops the column and upgrading again gives the same
codes. The migration's literal rule is compared with the reference rule of
``tests/support/pool_code.py`` (no application code derives a code).
"""

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tests.support.pool_code import derive_pool_code

MIGRATION = Path(__file__).parent.parent / "migrations" / "versions" / "0110_capacity_pool_code.py"
NAMES = [
    # organisation, name, mode
    (1, "Loket", "counted"),
    (1, "loket", "counted"),  # clashes after lower-casing
    (1, "LOKET!", "counted"),  # and again
    (1, "Café Zuid", "counted"),  # accents are not transliterated
    (1, "  Twee  woorden  ", "counted"),
    (1, "x" * 80, "counted"),  # cut to 40
    (1, "x" * 80 + " 2", "counted"),  # the same 40 characters: the suffix makes room
    (1, "éé", "counted"),  # nothing left: pool
    (1, "---", "counted"),
    (1, "Balie", "legacy_group"),  # keeps NULL
    (2, "Loket", "counted"),  # another organisation starts again
    (1, "a" * 39 + " b", "counted"),  # the 40-character cut ends in a hyphen: trimmed
    (1, "a" * 39 + "-b", "counted"),  # the same code: the suffix replaces the hyphen
    (1, "Advies", "counted"),
    (1, "advies", "counted"),  # clashes with "Advies" only after lower-casing
    (1, "?! ...", "counted"),  # only punctuation
    (1, "\u00dcn\u00efcode \u00c9\u00e9n", "counted"),  # accents become hyphens
]
EXPECTED = [
    "loket",
    "loket-2",
    "loket-3",
    "caf-zuid",
    "twee-woorden",
    "x" * 40,
    "x" * 38 + "-2",
    "pool",
    "pool-2",
    None,
    "loket",
    "a" * 39,
    "a" * 38 + "-2",
    "advies",
    "advies-2",
    "pool-3",
    "n-code-n",
]


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0109 holding the pools of ``NAMES``."""
    command.upgrade(alembic_config, "0109")
    path = tmp_path / "migrate.db"
    with closing(sqlite3.connect(path)) as database:
        for organisation in (1, 2):
            database.execute(
                "INSERT INTO organisations (id, name, slug, idp_key) VALUES (?, ?, ?, ?)",
                (organisation, f"Org {organisation}", f"org{organisation}", f"org{organisation}"),
            )
        database.execute(
            "INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,"
            " min_notice_minutes, horizon_days, phone_mode, dispatch_kind, idp_key)"
            " VALUES (1, 1, 'Balie', 'balie', 'Europe/Amsterdam', 30, 60, 60, 'optional',"
            " 'contact_center', 'balie')"
        )
        for number, (organisation, name, mode) in enumerate(NAMES, start=1):
            database.execute(
                "INSERT INTO capacity_pools (id, organisation_id, name, mode, group_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (number, organisation, name, mode, 1 if mode == "legacy_group" else None),
            )
        database.commit()
    return path


def codes(path: Path) -> list[object]:
    """Return the codes of the pools in id order."""
    with closing(sqlite3.connect(path)) as database:
        return [row[0] for row in database.execute("SELECT code FROM capacity_pools ORDER BY id")]


def test_counted_pools_get_a_unique_code_and_legacy_pools_none(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0110")

    assert codes(old_database) == EXPECTED


def test_downgrade_drops_the_column_and_upgrading_again_gives_the_same_codes(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0110")
    command.downgrade(alembic_config, "0109")
    with closing(sqlite3.connect(old_database)) as database:
        columns = [row[1] for row in database.execute("PRAGMA table_info(capacity_pools)")]
        indexes = [row[1] for row in database.execute("PRAGMA index_list(capacity_pools)")]
    assert "code" not in columns
    assert "uq_capacity_pools_organisation_id_code" not in indexes
    assert len(columns) == 6

    command.upgrade(alembic_config, "head")

    assert codes(old_database) == EXPECTED


def test_the_index_refuses_a_second_code_in_an_organisation_but_not_a_second_null(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0110")
    with closing(sqlite3.connect(old_database)) as database:
        insert = (
            "INSERT INTO capacity_pools (organisation_id, name, mode, code)"
            " VALUES (?, ?, 'counted', ?)"
        )
        database.execute(insert, (2, "Nieuw", "nieuw"))
        database.execute(insert, (2, "Nul", None))
        database.execute(insert, (2, "Nul twee", None))
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(insert, (2, "Dubbel", "nieuw"))


def test_the_literal_rule_of_the_migration_is_the_rule_of_the_domain() -> None:
    spec = importlib.util.spec_from_file_location("migration_0110", MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for _, name, _ in NAMES:
        assert module._base(name) == derive_pool_code(name)  # noqa: SLF001
    assert module.CODE_LENGTH == 40
