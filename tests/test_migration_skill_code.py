"""Migration 0112 (ADR 0121 step 4b): the contact-center code of a skill, expand only.

Upgrading adds a nullable column and a partial unique index without touching a row: a skill the
previous image wrote keeps loading and has no code; two skills of one organisation cannot share a
code, two organisations can, and any number of skills have none; downgrading drops both.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

SKILL = (
    "INSERT INTO skills (organisation_id, code, labels, contact_center_code) VALUES (?, ?, ?, ?)"
)
LABELS = '{"en": "x", "nl": "x"}'


def _skill_columns(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as database:
        return [row[1] for row in database.execute("PRAGMA table_info(skills)")]


def test_the_code_is_added_unique_per_organisation_and_dropped_again(
    alembic_config: Config, tmp_path: Path
) -> None:
    path = tmp_path / "migrate.db"
    command.upgrade(alembic_config, "0111")
    with closing(sqlite3.connect(path)) as database:
        for organisation in (1, 2):
            database.execute(
                "INSERT INTO organisations (id, name, slug, idp_key) VALUES (?, ?, ?, ?)",
                (organisation, f"Org {organisation}", f"org{organisation}", f"org{organisation}"),
            )
        database.execute(
            "INSERT INTO skills (organisation_id, code, labels) VALUES (1, 'old', ?)", (LABELS,)
        )
        database.commit()

    command.upgrade(alembic_config, "0112")

    with closing(sqlite3.connect(path)) as database:
        assert database.execute("SELECT contact_center_code FROM skills").fetchall() == [(None,)]
        database.execute(SKILL, (1, "a", LABELS, None))
        database.execute(SKILL, (1, "b", LABELS, "lang_de"))
        database.execute(SKILL, (2, "b", LABELS, "lang_de"))
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(SKILL, (1, "c", LABELS, "lang_de"))
        database.commit()

    command.downgrade(alembic_config, "0111")

    assert "contact_center_code" not in _skill_columns(path)
