"""Migration 0119 (ADR 0123 step 6a): a service chooses the call page and its option.

An expand step (ADR 0116): ``join_client`` may be ``embedded`` and ``embedded_call_webex_option``
is added empty, which means "as the organisation": nothing is backfilled, so every group follows
its organisation exactly as before. Downgrading gives every embedded group the default client and
drops the option.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

NOW = "2026-01-01 00:00:00.000000"
GROUP = (
    "INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,"
    " min_notice_minutes, horizon_days, phone_mode, dispatch_kind, idp_key)"
    " VALUES ({id}, {org}, 'Balie', '{slug}', 'Europe/Amsterdam', 30, 60, 60, 'optional',"
    " 'contact_center', '{slug}')"
)


@pytest.fixture
def old_database(alembic_config: Config, tmp_path: Path) -> Path:
    """Return a database at 0118: two organisations with a stored value, one without."""
    command.upgrade(alembic_config, "0118")
    path = tmp_path / "migrate.db"
    with closing(sqlite3.connect(path)) as database:
        for org, slug in ((1, "acme"), (2, "voorbeeld"), (3, "demo")):
            database.execute(
                "INSERT INTO organisations (id, name, slug, idp_key) VALUES (?, ?, ?, ?)",
                (org, slug.title(), slug, slug),
            )
        for group, org in ((1, 1), (2, 1), (3, 2), (4, 3)):
            database.execute(GROUP.format(id=group, org=org, slug=f"balie-{group}"))
        for org, value in ((1, '"always"'), (2, '"on_failure"')):
            database.execute(
                "INSERT INTO organisation_settings (organisation_id, key, value, updated_at)"
                " VALUES (?, 'embedded_call_webex_option', ?, ?)",
                (org, value, NOW),
            )
        database.commit()
    return path


def _options(path: Path) -> list[tuple[int, str | None]]:
    with closing(sqlite3.connect(path)) as database:
        return database.execute(
            "SELECT id, embedded_call_webex_option FROM groups ORDER BY id"
        ).fetchall()


def test_no_group_gets_a_value_of_its_own_so_every_group_follows_its_organisation(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0119")

    # Nothing is backfilled: the organisations' stored values stay where they are and still hold.
    assert _options(old_database) == [(1, None), (2, None), (3, None), (4, None)]
    with closing(sqlite3.connect(old_database)) as database:
        assert database.execute("SELECT DISTINCT join_client FROM groups").fetchall() == [
            ("browser",)
        ]
        assert database.execute(
            "SELECT organisation_id, value FROM organisation_settings ORDER BY organisation_id"
        ).fetchall() == [(1, '"always"'), (2, '"on_failure"')]


def test_embedded_and_the_options_are_the_only_values_the_checks_accept(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0119")

    with closing(sqlite3.connect(old_database)) as database:
        database.execute("UPDATE groups SET join_client = 'embedded' WHERE id = 1")
        database.execute("UPDATE groups SET embedded_call_webex_option = 'hidden' WHERE id = 1")
        for statement in (
            "UPDATE groups SET join_client = 'popup' WHERE id = 2",
            "UPDATE groups SET embedded_call_webex_option = 'sometimes' WHERE id = 2",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                database.execute(statement)


def test_the_downgrade_gives_embedded_groups_the_default_client_and_goes_up_again(
    alembic_config: Config, old_database: Path
) -> None:
    command.upgrade(alembic_config, "0119")
    with closing(sqlite3.connect(old_database)) as database:
        database.execute("UPDATE groups SET join_client = 'embedded' WHERE id = 1")
        database.execute("UPDATE groups SET join_client = 'choice' WHERE id = 3")
        database.commit()

    command.downgrade(alembic_config, "0118")

    with closing(sqlite3.connect(old_database)) as database:
        columns = [row[1] for row in database.execute("PRAGMA table_info(groups)")]
        assert "embedded_call_webex_option" not in columns
        assert database.execute("SELECT id, join_client FROM groups ORDER BY id").fetchall() == [
            (1, "browser"),
            (2, "browser"),
            (3, "choice"),
            (4, "browser"),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE groups SET join_client = 'embedded' WHERE id = 2")
        assert database.execute("SELECT COUNT(*) FROM organisation_settings").fetchone() == (2,)
    command.upgrade(alembic_config, "0119")
    assert _options(old_database) == [(1, None), (2, None), (3, None), (4, None)]
