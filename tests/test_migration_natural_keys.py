"""Migration 0099: closure scenarios and alert targets get natural keys (ADR 0099 point 2)."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BEFORE = """
INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
    min_notice_minutes, horizon_days, phone_mode)
    VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
INSERT INTO closure_scenarios (id, organisation_id, group_id, name, notices)
    VALUES (1, 1, NULL, 'Storing', '{}'), (2, 1, NULL, 'Storing', '{}'),
           (3, 1, NULL, 'Storing', '{}'), (4, 1, NULL, 'Storing (2)', '{}'),
           (5, 1, 1, 'Storing', '{}'), (6, 1, 1, 'Storing', '{}');
INSERT INTO alert_targets (id, organisation_id, kind, address, min_level)
    VALUES (1, NULL, 'email', 'ops@voorbeeld.nl', 'warning'),
           (2, NULL, 'email', 'ops@voorbeeld.nl', 'outage'),
           (3, 1, 'email', 'ops@voorbeeld.nl', 'warning'),
           (4, 1, 'email', 'ops@voorbeeld.nl', 'warning');
INSERT INTO alert_deliveries (target_id, alert_key, token) VALUES (1, 'a', 't'), (2, 'a', 't');
"""
SCENARIO = (
    "INSERT INTO closure_scenarios (organisation_id, group_id, name, notices)"
    " VALUES (1, {group}, '{name}', '{{}}')"
)


def test_duplicates_are_made_distinct_and_the_names_are_then_unique(
    alembic_config: Config,
    tmp_path: Path,
) -> None:
    command.upgrade(alembic_config, "0098")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(BEFORE)

    command.upgrade(alembic_config, "head")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        names = dict(database.execute("SELECT id, name FROM closure_scenarios"))
        targets = [row[0] for row in database.execute("SELECT id FROM alert_targets ORDER BY id")]
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(SCENARIO.format(group="NULL", name="Storing"))
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(SCENARIO.format(group="1", name="Storing"))
        # an organisation scenario and a group scenario may share a name
        database.execute(SCENARIO.format(group="1", name="Eigen"))
        database.execute(SCENARIO.format(group="NULL", name="Eigen"))
    assert names == {
        1: "Storing",
        2: "Storing (3)",  # "Storing (2)" is taken by scenario 4
        3: "Storing (4)",
        4: "Storing (2)",
        5: "Storing",
        6: "Storing (2)",
    }
    assert targets == [1, 3]  # the first of each repeated kind and address stays
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        deliveries = [r[0] for r in database.execute("SELECT target_id FROM alert_deliveries")]
    assert deliveries == [1]  # the deleted target's delivery went with it


def test_a_long_name_keeps_its_suffix_within_the_column(
    alembic_config: Config,
    tmp_path: Path,
) -> None:
    long = "x" * 80
    command.upgrade(alembic_config, "0098")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.execute("INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme')")
        for _ in range(2):
            database.execute(
                "INSERT INTO closure_scenarios (organisation_id, group_id, name, notices)"
                " VALUES (1, NULL, ?, '{}')",
                (long,),
            )
        database.commit()

    command.upgrade(alembic_config, "head")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        names = [
            row[0] for row in database.execute("SELECT name FROM closure_scenarios ORDER BY id")
        ]
    assert names == [long, "x" * 76 + " (2)"]


def test_the_downgrade_drops_the_indexes(
    alembic_config: Config,
    tmp_path: Path,
) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0098")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.execute("INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme')")
        database.execute(SCENARIO.format(group="NULL", name="Storing"))
        database.execute(SCENARIO.format(group="NULL", name="Storing"))
