from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}")
    monkeypatch.setenv("ENVIRONMENT", "test")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_upgrade_and_downgrade_on_fresh_database(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_single_head(alembic_config: Config) -> None:
    assert len(ScriptDirectory.from_config(alembic_config).get_heads()) == 1


def test_models_match_migrations(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)


def test_upgrade_and_downgrade_with_data(alembic_config: Config, tmp_path: Path) -> None:
    # SQLite rebuilds tables for some ALTERs; with foreign keys on, that must still work
    # when rows reference the rebuilt table (an empty database hides the problem).
    import sqlite3

    command.upgrade(alembic_config, "0006")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode, wxcc_queue_id)
            VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional', 'q-1');
        INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)
            VALUES (1, 0, '09:00:00', '10:00:00', 1);
        INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)
            VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 1);
        INSERT INTO bookings (id, slot_id, group_id, status, locale, management_token_hash,
            management_token_salt, created_at, consents)
            VALUES (1, 1, 1, 'confirmed', 'nl', 'h', 's', '2026-01-01 08:00:00', '');
        """
    )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0006")
    command.upgrade(alembic_config, "head")

    database = sqlite3.connect(tmp_path / "migrate.db")
    routing = database.execute("SELECT wxcc_routing_value FROM groups").fetchone()
    booking = database.execute("SELECT slot_id FROM bookings").fetchone()
    database.close()
    assert routing == ("q-1",)  # the old queue id became the routing value
    assert booking == (1,)


def test_blocks_without_agents_survive_upgrade_and_are_removed_on_downgrade(
    alembic_config: Config, tmp_path: Path
) -> None:
    import sqlite3

    command.upgrade(alembic_config, "0017")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode)
            VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
        INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)
            VALUES (1, 0, '09:00:00', '12:00:00', 3);
        INSERT INTO schedule_overrides (group_id, day, opens, closes, capacity)
            VALUES (1, '2026-01-07', '09:00:00', '10:00:00', 2);
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)"
            " VALUES (1, 0, '12:00:00', '12:30:00', 0)"
        )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "0018")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity, label)
            VALUES (1, 0, '12:00:00', '12:30:00', 0, 'Lunch');
        INSERT INTO schedule_overrides (group_id, day, opens, closes, capacity, label)
            VALUES (1, '2026-01-08', '09:00:00', '10:00:00', 0, 'Heidag');
        INSERT INTO schedule_overrides (group_id, day) VALUES (1, '2026-01-09');
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)"
            " VALUES (1, 1, '09:00:00', '10:00:00', -1)"
        )
    database.commit()
    database.close()

    command.downgrade(alembic_config, "0017")
    database = sqlite3.connect(tmp_path / "migrate.db")
    rules = database.execute("SELECT capacity FROM schedule_rules").fetchall()
    overrides = database.execute("SELECT day, capacity FROM schedule_overrides").fetchall()
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)"
            " VALUES (1, 2, '09:00:00', '10:00:00', 0)"
        )
    database.close()
    assert rules == [(3,)]
    assert sorted(overrides) == [("2026-01-07", 2), ("2026-01-09", None)]


def test_stored_sms_settings_become_webex_connect_and_back(
    alembic_config: Config, tmp_path: Path
) -> None:
    import json
    import sqlite3

    command.upgrade(alembic_config, "0018")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
        INSERT INTO organisations (id, name, slug) VALUES (2, 'Andere', 'andere');
        INSERT INTO channel_settings (organisation_id, channel, settings, updated_at)
            VALUES (1, 'sms', '{"api_base_url": "https://api.us.webexconnect.io",
                "country_code": "31", "sender_id": "Demo"}', '2026-01-01 08:00:00');
        INSERT INTO channel_settings (organisation_id, channel, settings, updated_at)
            VALUES (1, 'email', '{"host": "smtp.demo.nl"}', '2026-01-01 08:00:00');
        """
    )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "0019")
    database = sqlite3.connect(tmp_path / "migrate.db")
    sms = json.loads(
        database.execute("SELECT settings FROM channel_settings WHERE channel = 'sms'").fetchone()[
            0
        ]
    )
    email = json.loads(
        database.execute(
            "SELECT settings FROM channel_settings WHERE channel = 'email'"
        ).fetchone()[0]
    )
    database.execute(
        "INSERT INTO channel_settings (organisation_id, channel, settings, updated_at) VALUES"
        ' (2, \'sms\', \'{"provider": "other", "sender_id": "X"}\', \'2026-01-01 08:00:00\')'
    )
    database.commit()
    database.close()
    assert sms["provider"] == "webex_connect"
    assert "provider" not in email

    command.downgrade(alembic_config, "0018")
    database = sqlite3.connect(tmp_path / "migrate.db")
    rows = database.execute(
        "SELECT organisation_id, settings FROM channel_settings WHERE channel = 'sms'"
    ).fetchall()
    database.close()
    assert [org for org, _ in rows] == [1]  # another provider would go through Connect
    assert "provider" not in json.loads(rows[0][1])


def test_an_existing_database_is_switched_to_incremental_vacuum(
    alembic_config: Config, tmp_path: Path
) -> None:
    # ADR 0040: databases created before the connect pragma are converted once, at migration.
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as old:
        old.execute("PRAGMA journal_mode=WAL")
        old.execute("CREATE TABLE legacy (a)")
        old.commit()

    command.upgrade(alembic_config, "head")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        assert database.execute("PRAGMA auto_vacuum").fetchone() == (2,)


def test_closure_content_goes_on_downgrade_and_the_notices_stay(
    alembic_config: Config, tmp_path: Path
) -> None:
    # ADR 0037: downgrading keeps only what ADR 0024 had, the closure and its notices.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "0025")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
            INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
                min_notice_minutes, horizon_days, phone_mode)
                VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
            INSERT INTO group_contacts (group_id, body) VALUES (1, '{"options": []}');
            INSERT INTO closures (id, group_id, starts_at, created_at, contacts)
                VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-01 08:00:00', '{"options": []}');
            INSERT INTO closure_notices (closure_id, locale, text, heading)
                VALUES (1, 'nl', 'Gesloten.', 'Storing'), (1, 'en', 'Closed.', NULL);
            """
        )

    command.downgrade(alembic_config, "0024")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        notices = database.execute("SELECT locale, text FROM closure_notices").fetchall()
        closures = database.execute("SELECT id, group_id FROM closures").fetchall()
        tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master")}
    assert sorted(notices) == [("en", "Closed."), ("nl", "Gesloten.")]
    assert closures == [(1, 1)]
    assert "group_contacts" not in tables


def test_existing_groups_are_not_embeddable_and_origins_go_on_downgrade(
    alembic_config: Config, tmp_path: Path
) -> None:
    # ADR 0035: the migration makes nothing embeddable; downgrading drops the lists.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "0035")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
            INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
                min_notice_minutes, horizon_days, phone_mode)
                VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
            """
        )

    command.upgrade(alembic_config, "0037")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        assert database.execute("SELECT embedding_enabled FROM groups").fetchall() == [(0,)]
        database.execute(
            "INSERT INTO organisation_origins (organisation_id, origin, position)"
            " VALUES (1, 'https://www.gemeente.nl', 0)"
        )
        database.commit()

    command.downgrade(alembic_config, "0035")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master")}
        columns = {r[1] for r in database.execute("PRAGMA table_info(groups)")}
    assert "organisation_origins" not in tables
    assert "embedding_enabled" not in columns


def test_existing_audit_events_get_an_id_level_and_category(
    alembic_config: Config, tmp_path: Path
) -> None:
    # ADR 0040, part 2: rows from before the event schema are filled from outcome and source.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "0025")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO audit_events (at, actor, action, outcome, source)
                VALUES ('2026-10-01 08:00:00', 'sub-1', 'admin.login', 'denied', 'admin'),
                       ('2026-10-01 08:01:00', 'system', 'job.send_reminder', 'failure', 'system'),
                       ('2026-10-01 08:02:00', 'system', 'job.purge_pii', 'success', 'system');
            """
        )

    command.upgrade(alembic_config, "head")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        rows = database.execute(
            "SELECT event_id, level, category, trace_id FROM audit_events ORDER BY at"
        ).fetchall()
    assert [(level, category) for _, level, category, _ in rows] == [
        ("warning", "audit"),
        ("error", "system"),
        ("info", "system"),
    ]
    assert len({event_id for event_id, *_ in rows}) == 3
    assert all(len(event_id) == 32 and trace == "" for event_id, _, _, trace in rows)


def test_diagnostic_modes_go_on_downgrade(alembic_config: Config, tmp_path: Path) -> None:
    # ADR 0040, part 4.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0034")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master")}
    assert "diagnostic_modes" not in tables
    assert "ix_audit_events_category_at" not in tables


def test_custom_roles_go_on_downgrade(alembic_config: Config, tmp_path: Path) -> None:
    # ADR 0039: the downgrade drops custom roles; their holders fall back to no role.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "0030")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.execute(
            "INSERT INTO roles (template, name_nl, name_en, claim_value, permissions, version)"
            " VALUES ('group_admin', 'Teamleider', 'Team lead', 'lead', 'group.view', 1)"
        )
        database.commit()

    command.downgrade(alembic_config, "0040")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master")}
    assert "roles" not in tables


def test_the_name_is_split_on_upgrade_and_joined_on_downgrade(
    alembic_config: Config, tmp_path: Path
) -> None:
    # ADR 0055: first word to first_name, the rest to last_name; answers go on downgrade.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "0052")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Gemeente', 'gemeente');
            INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
                min_notice_minutes, horizon_days, phone_mode)
                VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
            INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)
                VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 2);
            INSERT INTO bookings (id, slot_id, group_id, status, name, email, locale,
                management_token_hash, created_at)
                VALUES (1, 1, 1, 'confirmed', 'Ada Augusta Lovelace', 'a@example.org', 'nl',
                    'h1', '2026-01-01 08:00:00'),
                (2, 1, 1, 'confirmed', 'Cher', 'c@example.org', 'nl', 'h2',
                    '2026-01-01 08:00:00'),
                (3, 1, 1, 'cancelled', NULL, NULL, 'nl', 'h3', '2026-01-01 08:00:00');
            """
        )

    command.upgrade(alembic_config, "head")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        split = database.execute(
            "SELECT first_name, last_name FROM bookings ORDER BY id"
        ).fetchall()
        database.execute("UPDATE bookings SET form_answers = '{}' WHERE id = 1")
        database.commit()
    assert split == [("Ada", "Augusta Lovelace"), ("Cher", ""), (None, None)]

    command.downgrade(alembic_config, "0052")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        joined = database.execute("SELECT name FROM bookings ORDER BY id").fetchall()
        tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master")}
    assert joined == [("Ada Augusta Lovelace",), ("Cher",), (None,)]
    assert "group_form_fields" not in tables
