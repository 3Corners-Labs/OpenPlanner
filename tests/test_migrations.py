from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


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
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
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
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
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
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
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
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
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
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
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
            " VALUES (1, 'https://www.acme.nl', 0)"
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

    command.upgrade(alembic_config, "0044")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
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

    command.downgrade(alembic_config, "0044")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        joined = database.execute("SELECT name FROM bookings ORDER BY id").fetchall()
        tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master")}
    assert joined == [("Ada Augusta Lovelace",), ("Cher",), (None,)]
    assert "group_form_fields" not in tables


def test_unconfirmed_bookings_are_cancelled_and_purged_on_downgrade(
    alembic_config: Config, tmp_path: Path
) -> None:
    import sqlite3

    command.upgrade(alembic_config, "0058")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode)
            VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
        INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)
            VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 2);
        INSERT INTO bookings (id, slot_id, group_id, status, locale, management_token_hash,
            management_token_salt, created_at, consents, confirm_by, confirmed_at)
            VALUES (1, 1, 1, 'pending_confirmation', 'nl', 'h1', 's', '2026-01-01 08:00:00',
                '', '2026-01-02 08:00:00', NULL),
            (2, 1, 1, 'confirmed', 'nl', 'h2', 's', '2026-01-01 08:00:00', '', NULL,
                '2026-01-01 09:00:00');
        """
    )
    database.commit()
    database.close()

    command.downgrade(alembic_config, "0057")

    database = sqlite3.connect(tmp_path / "migrate.db")
    statuses = database.execute("SELECT id, status FROM bookings ORDER BY id").fetchall()
    purges = database.execute("SELECT booking_id FROM jobs WHERE type = 'purge_pii'").fetchall()
    columns = {row[1] for row in database.execute("PRAGMA table_info(bookings)")}
    database.close()
    assert statuses == [(1, "cancelled"), (2, "confirmed")]
    assert purges == [(1,)]
    assert not {"confirm_by", "confirmed_at"} & columns


def test_the_direct_call_switch_becomes_an_offer_per_block_and_back(
    alembic_config: Config, tmp_path: Path
) -> None:
    """ADR 0072: the switch becomes blocks that offer both; the downgrade restores it."""
    import sqlite3

    command.upgrade(alembic_config, "0059")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode, direct_call_enabled)
            VALUES (1, 1, 'Direct', 'direct', 'Europe/Amsterdam', 30, 60, 60, 'optional', 1),
                   (2, 1, 'Gepland', 'gepland', 'Europe/Amsterdam', 30, 60, 60, 'optional', 0);
        INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity)
            VALUES (1, 0, '09:00:00', '12:00:00', 3), (2, 0, '09:00:00', '12:00:00', 3);
        INSERT INTO schedule_overrides (group_id, day, opens, closes, capacity)
            VALUES (1, '2026-11-02', '09:00:00', '10:00:00', 2);
        INSERT INTO schedule_overrides (group_id, day) VALUES (2, '2026-11-03');
        """
    )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "0064")
    database = sqlite3.connect(tmp_path / "migrate.db")
    rules = database.execute("SELECT group_id, offer FROM schedule_rules ORDER BY group_id")
    overrides = database.execute("SELECT group_id, offer FROM schedule_overrides ORDER BY group_id")
    assert rules.fetchall() == [(1, "both"), (2, "scheduled")]
    assert overrides.fetchall() == [(1, "both"), (2, "scheduled")]
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO schedule_rules (group_id, weekday, opens, closes, capacity, offer)"
            " VALUES (2, 1, '09:00:00', '10:00:00', 1, 'maybe')"
        )
    database.execute("UPDATE schedule_rules SET offer = 'direct' WHERE group_id = 2")
    database.commit()
    database.close()

    command.downgrade(alembic_config, "0059")
    database = sqlite3.connect(tmp_path / "migrate.db")
    switches = database.execute("SELECT id, direct_call_enabled FROM groups ORDER BY id")
    assert switches.fetchall() == [(1, 1), (2, 1)]
    database.close()


def test_the_change_lead_column_is_renamed_with_its_values_and_back(
    alembic_config: Config, tmp_path: Path
) -> None:
    """ADR 0077: the lead keeps its value under the new name, and the constraint follows."""
    import sqlite3

    command.upgrade(alembic_config, "0076")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode, participant_change_lead_minutes)
            VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional', 180);
        """
    )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "0077")
    database = sqlite3.connect(tmp_path / "migrate.db")
    lead = database.execute("SELECT participant_change_lead_minutes FROM groups WHERE id = 1")
    assert lead.fetchone() == (180,)
    with pytest.raises(sqlite3.IntegrityError, match="participant_lead"):
        database.execute("UPDATE groups SET participant_change_lead_minutes = 1441")
    database.close()

    command.downgrade(alembic_config, "0076")
    database = sqlite3.connect(tmp_path / "migrate.db")
    lead = database.execute("SELECT participant_change_lead_minutes FROM groups WHERE id = 1")
    assert lead.fetchone() == (180,)
    with pytest.raises(sqlite3.IntegrityError, match="participant_lead"):
        database.execute("UPDATE groups SET participant_change_lead_minutes = 1441")
    database.close()


def test_existing_bookings_get_distinct_public_references(
    alembic_config: Config, tmp_path: Path
) -> None:
    """ADR 0093: the migration names every booking that already exists, each differently."""
    import sqlite3

    command.upgrade(alembic_config, "0006")
    database = sqlite3.connect(tmp_path / "migrate.db")
    database.executescript(
        """
        INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
        INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
            min_notice_minutes, horizon_days, phone_mode, wxcc_queue_id)
            VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional', 'q-1');
        INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)
            VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 2);
        INSERT INTO bookings (id, slot_id, group_id, status, locale, management_token_hash,
            management_token_salt, created_at, consents)
            VALUES (1, 1, 1, 'confirmed', 'nl', 'h1', 's', '2026-01-01 08:00:00', ''),
                   (2, 1, 1, 'confirmed', 'nl', 'h2', 's', '2026-01-01 08:00:00', '');
        """
    )
    database.commit()
    database.close()

    command.upgrade(alembic_config, "head")

    database = sqlite3.connect(tmp_path / "migrate.db")
    refs = [row[0] for row in database.execute("SELECT public_ref FROM bookings ORDER BY id")]
    database.close()
    assert len(refs) == 2
    assert refs[0] != refs[1]
    assert all(len(ref) >= 22 for ref in refs)


def test_existing_destinations_keep_the_decisions_and_events_get_an_empty_role(
    alembic_config: Config, tmp_path: Path
) -> None:
    # ADR 0103 point 9: a destination that received authz lines as ordinary lines keeps them.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "0086")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO log_destinations (name, kind, address, min_level, categories,
                                          created_at, updated_at)
                VALUES ('a', 'syslog', 'syslog+tls://s:6514', 'info', 'audit,system',
                        '2026-11-01 08:00:00', '2026-11-01 08:00:00'),
                       ('b', 'http', 'https://c/x', 'info', '',
                        '2026-11-01 08:00:00', '2026-11-01 08:00:00');
            INSERT INTO audit_events (at, actor, action, outcome, source, event_id)
                VALUES ('2026-11-01 08:00:00', 'sub-1', 'group.edit', 'success', 'admin',
                        'e1');
            """
        )

    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        upgraded = database.execute("SELECT categories FROM log_destinations ORDER BY name")
        assert [r[0] for r in upgraded] == ["audit,authz,system", "authz"]
        assert database.execute("SELECT actor_role FROM audit_events").fetchone() == ("",)

    command.downgrade(alembic_config, "0086")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        downgraded = database.execute("SELECT categories FROM log_destinations ORDER BY name")
        assert [r[0] for r in downgraded] == ["audit,system", ""]
        columns = {r[1] for r in database.execute("PRAGMA table_info(audit_events)")}
    assert "actor_role" not in columns


def test_the_wxcc_routing_task_and_job_get_neutral_places_and_survive_a_downgrade(
    alembic_config: Config, tmp_path: Path
) -> None:
    """ADR 0032 CC-1 as an ADR 0116 expand step: copied forward, old values untouched."""
    import json
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "0096")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
            INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
                min_notice_minutes, horizon_days, phone_mode, wxcc_business_address,
                wxcc_channel, wxcc_routing_value, idp_key)
                VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional',
                        'team@example.biz', 'video', 'team-1', 'team'),
                       (2, 1, 'Half', 'half', 'Europe/Amsterdam', 30, 60, 60, 'optional',
                        'half@example.biz', NULL, NULL, 'half'),
                       (3, 1, 'None', 'none', 'Europe/Amsterdam', 30, 60, 60, 'optional',
                        NULL, NULL, NULL, 'none');
            INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)
                VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 2);
            INSERT INTO bookings (id, slot_id, group_id, status, locale, management_token_hash,
                management_token_salt, created_at, consents, public_ref, wxcc_task_id)
                VALUES (1, 1, 1, 'confirmed', 'nl', 'h1', 's', '2026-01-01 08:00:00', '',
                        'ref-1', 'task-1'),
                       (2, 1, 1, 'confirmed', 'nl', 'h2', 's', '2026-01-01 08:00:00', '',
                        'ref-2', NULL);
            INSERT INTO jobs (booking_id, type, run_at, status)
                VALUES (1, 'dispatch_wxcc_task', '2026-01-05 07:55:00', 'pending'),
                       (2, 'purge_pii', '2026-01-06 08:30:00', 'pending');
            """
        )
        database.commit()

    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        routings = database.execute(
            "SELECT group_id, kind, routing_value, attributes FROM group_contact_center "
            "ORDER BY group_id"
        ).fetchall()
        tasks = database.execute(
            "SELECT contact_center_task_id, contact_center_kind, wxcc_task_id FROM bookings "
            "ORDER BY id"
        ).fetchall()
        jobs = database.execute("SELECT type FROM jobs ORDER BY booking_id").fetchall()
    assert [(g, k, r, json.loads(a)) for g, k, r, a in routings] == [
        (1, "webex_cc", "team-1", {"business_address": "team@example.biz", "channel": "video"}),
        (2, "webex_cc", None, {"business_address": "half@example.biz"}),
    ]
    assert tasks == [("task-1", "webex_cc", "task-1"), (None, None, None)]
    assert jobs == [("dispatch_wxcc_task",), ("purge_pii",)]  # the previous image reads it

    command.downgrade(alembic_config, "0096")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        groups = database.execute(
            "SELECT wxcc_business_address, wxcc_channel, wxcc_routing_value FROM groups ORDER BY id"
        ).fetchall()
        task = database.execute("SELECT wxcc_task_id FROM bookings WHERE id = 1").fetchone()
        jobs = database.execute("SELECT type FROM jobs ORDER BY booking_id").fetchall()
        tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master")}
    assert groups == [
        ("team@example.biz", "video", "team-1"),
        ("half@example.biz", None, None),
        (None, None, None),
    ]
    assert task == ("task-1",)
    assert jobs == [("dispatch_wxcc_task",), ("purge_pii",)]
    assert "group_contact_center" not in tables


def test_refused_bookings_become_cancelled_with_a_purge_due_on_downgrade(
    alembic_config: Config, tmp_path: Path
) -> None:
    # ADR 0066 step 8: without the status the rows cannot exist; the personal data goes at once.
    import sqlite3
    from contextlib import closing

    command.upgrade(alembic_config, "head")
    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        database.executescript(
            """
            INSERT INTO organisations (id, name, slug) VALUES (1, 'Acme', 'acme');
            INSERT INTO groups (id, organisation_id, name, slug, timezone, slot_minutes,
                min_notice_minutes, horizon_days, phone_mode)
                VALUES (1, 1, 'Team', 'team', 'Europe/Amsterdam', 30, 60, 60, 'optional');
            INSERT INTO slots (id, group_id, start, "end", capacity_snapshot)
                VALUES (1, 1, '2026-01-05 08:00:00', '2026-01-05 08:30:00', 1);
            INSERT INTO bookings (id, slot_id, group_id, status, locale, management_token_hash,
                management_token_salt, created_at, consents, public_ref)
                VALUES (1, 1, 1, 'refused', 'nl', 'h', 's', '2026-01-01 08:00:00', '', 'fx0000000001');
            """
        )
        database.commit()

    command.downgrade(alembic_config, "0105")

    with closing(sqlite3.connect(tmp_path / "migrate.db")) as database:
        status = database.execute("SELECT status, cancelled_at FROM bookings").fetchone()
        jobs = database.execute("SELECT type, status FROM jobs").fetchall()
    assert status == ("cancelled", "2026-01-01 08:00:00")
    assert jobs == [("purge_pii", "pending")]
