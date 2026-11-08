"""The platform aggregate of a configuration document on SQLite (ADR 0099 step 2)."""

import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_platform import WRITERS, read_platform
from app.adapters.db.log_destinations import secret_name
from app.adapters.db.models import (
    ApiSettingsRow,
    HolidayCalendarRow,
    HolidayRow,
    IntegrationCredentialRow,
    LogDestinationRow,
    LogDestinationStatusRow,
    PlatformSettingRow,
    RoleRow,
)
from app.domain.configuration_changes import Change, ChangeAction, RuleContext, Usage
from app.domain.configuration_document import Document, Mode, Scope, parse_document, to_json
from app.domain.configuration_plan import plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS, V1
from app.domain.configuration_values import Secret, Value
from app.domain.errors import DomainError, ErrorCode
from tests.support.configuration import allow

BOX = SecretBox(b"s" * 32)
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def change(
    kind: str,
    key: tuple[str, ...],
    action: ChangeAction,
    values: dict[str, Value] | None = None,
    fields: tuple[str, ...] | None = None,
    address: tuple[str, ...] | None = None,
) -> Change:
    given = values or {}
    return Change(
        kind=kind,
        key=key,
        address=address or key,
        path="",
        action=action,
        fields=tuple(given) if fields is None else fields,
        values=given,
    )


async def apply(engine: AsyncEngine, box: SecretBox | None, *changes: Change) -> None:
    async with AsyncSession(engine) as session, session.begin():
        for item in changes:
            await WRITERS[item.kind](session, box, item)


async def read(engine: AsyncEngine, box: SecretBox | None = BOX) -> Entity:
    async with AsyncSession(engine) as session:
        return await read_platform(session, box)


def children(platform: Entity, name: str) -> dict[tuple[str, ...], Entity]:
    return {e.key: e for e in platform.children[name]}


async def seed(engine: AsyncEngine) -> None:
    """A platform with one of everything, written through the writers."""
    await apply(
        engine,
        BOX,
        change(
            "platform_setting", ("rate_limit_bookings",), ChangeAction.CREATE, {"value": "5/hour"}
        ),
        change(
            "platform_secret", ("smtp_password",), ChangeAction.CREATE, {"secret": Secret("pw-1")}
        ),
        change(
            "platform_secret",
            ("webex_service_app",),
            ChangeAction.CREATE,
            {"secret": Secret(json.dumps({"token": "t", "refresh": "r"}))},
        ),
        change("api_settings", (), ChangeAction.CREATE, {"enabled": True, "docs_public": False}),
        change(
            "log_destination",
            ("splunk",),
            ChangeAction.CREATE,
            {
                "kind": "splunk_hec",
                "address": "https://splunk.voorbeeld.nl:8088",
                "index_name": "main",
                "source": "openplanner",
                "min_level": "info",
                "categories": "audit,system",
                "include_diagnostic": False,
                "include_trace": False,
                "enabled": True,
                "ca_pem": "",
                "secret": Secret("hec-token"),
            },
        ),
        change(
            "role",
            ("planner",),
            ChangeAction.CREATE,
            {
                "template": "org_admin",
                "name_nl": "Planner",
                "name_en": "Planner",
                "permissions": ["audit.view", "dashboard.view"],
            },
        ),
        change("holiday_calendar", ("nl",), ChangeAction.CREATE, {"name": "Nederland"}),
        change(
            "holiday",
            ("2026-12-25",),
            ChangeAction.CREATE,
            {"name": "Kerst"},
            address=("nl", "2026-12-25"),
        ),
        change(
            "holiday",
            ("2026-12-26",),
            ChangeAction.CREATE,
            {"name": "Tweede kerstdag"},
            address=("nl", "2026-12-26"),
        ),
    )


async def test_the_platform_is_read_in_the_form_the_kinds_expect(engine: AsyncEngine) -> None:
    await seed(engine)
    async with AsyncSession(engine) as session, session.begin():
        session.add_all(
            [
                PlatformSettingRow(key="log_level", value='"DEBUG"', updated_at=NOW),
                PlatformSettingRow(key="not_a_setting", value="1", updated_at=NOW),
                PlatformSettingRow(key="admin_session_hours", value="not json", updated_at=NOW),
                PlatformSettingRow(key="product_footer", value="[1]", updated_at=NOW),
            ]
        )

    platform = await read(engine)

    assert (platform.kind, platform.key) == ("platform", ())
    assert {k: e.fields for k, e in children(platform, "settings").items()} == {
        ("rate_limit_bookings",): {"value": "5/hour"}
    }
    secrets = children(platform, "secrets")
    assert secrets[("smtp_password",)].fields["secret"] == Secret("pw-1")
    assert secrets[("webex_service_app",)].fields["secret"] == Secret(
        json.dumps({"refresh": "r", "token": "t"}, sort_keys=True)
    )
    assert platform.children["api"][0].fields == {"enabled": True, "docs_public": False}
    destination = children(platform, "log_destinations")[("splunk",)]
    assert destination.fields["secret"] == Secret("hec-token")
    assert destination.fields["categories"] == "audit,system"
    assert children(platform, "roles")[("planner",)].fields["permissions"] == [
        "audit.view",
        "dashboard.view",
    ]
    calendar = children(platform, "holiday_calendars")[("nl",)]
    assert calendar.fields == {"name": "Nederland"}
    assert [(d.key, d.fields["name"]) for d in calendar.children["days"]] == [
        (("2026-12-25",), "Kerst"),
        (("2026-12-26",), "Tweede kerstdag"),
    ]


async def test_an_empty_platform_has_the_switches_off_and_nothing_else(engine: AsyncEngine) -> None:
    platform = await read(engine)

    assert platform.children["api"][0].fields == {"enabled": False, "docs_public": False}
    for name in ("settings", "secrets", "log_destinations", "roles", "holiday_calendars"):
        assert platform.children[name] == ()


async def test_without_a_settings_key_no_secret_is_read(engine: AsyncEngine) -> None:
    await seed(engine)

    platform = await read(engine, None)

    assert platform.children["secrets"] == ()
    assert "secret" not in children(platform, "log_destinations")[("splunk",)].fields


async def test_a_credential_that_does_not_open_is_left_out(engine: AsyncEngine) -> None:
    await seed(engine)

    platform = await read(engine, SecretBox(b"x" * 32))

    assert platform.children["secrets"] == ()
    assert "secret" not in children(platform, "log_destinations")[("splunk",)].fields


async def test_a_credential_row_a_document_does_not_hold_is_left_out(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        for name in ("setting:webex_webhook_secret", "something_else"):
            nonce, ciphertext = BOX.seal(name, json.dumps({"value": "x"}).encode())
            session.add(
                IntegrationCredentialRow(
                    name=name, nonce=nonce, ciphertext=ciphertext, updated_at=NOW
                )
            )
        nonce, ciphertext = BOX.seal("setting:smtp_password", b"not json")
        session.add(
            IntegrationCredentialRow(
                name="setting:smtp_password", nonce=nonce, ciphertext=ciphertext, updated_at=NOW
            )
        )

    assert (await read(engine)).children["secrets"] == ()


def as_document(platform: Entity) -> Document:
    root = Entity("installation", (), children={"platform": (platform,)})
    return Document("openplanner.config/v1", Mode.MERGE, Scope(), root, "digest", V1)


def plain(node: Entity) -> Entity:
    """The tree without its secrets, as a document written without secrets holds it."""
    fields = {k: v for k, v in node.fields.items() if k != "secret"}
    kids = {
        name: () if name == "secrets" else tuple(plain(child) for child in found)
        for name, found in node.children.items()
    }
    return Entity(node.kind, node.key, node.action, fields, kids, node.path)


def plan_against(document: Document, current: Entity):  # type: ignore[no-untyped-def]
    root = Entity("installation", (), children={"platform": (current,)})
    return plan_document(
        document,
        root,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(production=False),
        settings_version=1,
    )


async def test_a_document_made_from_the_read_tree_plans_to_no_change(engine: AsyncEngine) -> None:
    await seed(engine)
    platform = await read(engine)

    result = plan_against(as_document(platform), platform)

    assert result.refusals == ()
    assert result.changes == ()


async def test_a_written_out_document_reads_back_and_plans_to_no_change(
    engine: AsyncEngine,
) -> None:
    await seed(engine)
    platform = await read(engine)
    written = to_json(as_document(plain(platform)))

    document = parse_document(json.loads(json.dumps(written)), FORMATS)
    result = plan_against(document, platform)

    assert result.refusals == ()
    assert result.changes == ()


async def test_a_setting_is_written_changed_and_reset(engine: AsyncEngine) -> None:
    key = ("rate_limit_bookings",)
    await apply(
        engine, BOX, change("platform_setting", key, ChangeAction.CREATE, {"value": "5/hour"})
    )
    await apply(
        engine, BOX, change("platform_setting", key, ChangeAction.UPDATE, {"value": "6/hour"})
    )

    assert children(await read(engine), "settings")[key].fields == {"value": "6/hour"}
    await apply(engine, BOX, change("platform_setting", key, ChangeAction.DELETE))
    assert (await read(engine)).children["settings"] == ()


async def test_a_setting_value_that_is_not_json_is_refused(engine: AsyncEngine) -> None:
    with pytest.raises(DomainError) as refused:
        await apply(
            engine,
            BOX,
            change("platform_setting", ("x",), ChangeAction.CREATE, {"value": Secret("no")}),
        )

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_a_secret_is_sealed_under_the_name_the_portal_uses(engine: AsyncEngine) -> None:
    await apply(
        engine,
        BOX,
        change(
            "platform_secret", ("smtp_password",), ChangeAction.CREATE, {"secret": Secret("pw")}
        ),
    )

    async with AsyncSession(engine) as session:
        row = await session.get(IntegrationCredentialRow, "setting:smtp_password")
    assert row is not None
    assert json.loads(BOX.open("setting:smtp_password", row.nonce, row.ciphertext)) == {
        "value": "pw"
    }
    assert b"pw" not in row.ciphertext


async def test_a_secret_is_replaced_and_removed(engine: AsyncEngine) -> None:
    key = ("smtp_password",)
    await apply(
        engine, BOX, change("platform_secret", key, ChangeAction.CREATE, {"secret": Secret("a")})
    )
    await apply(
        engine, BOX, change("platform_secret", key, ChangeAction.UPDATE, {"secret": Secret("b")})
    )
    assert children(await read(engine), "secrets")[key].fields["secret"] == Secret("b")

    await apply(engine, BOX, change("platform_secret", key, ChangeAction.UPDATE, {"secret": None}))
    assert (await read(engine)).children["secrets"] == ()

    await apply(
        engine, BOX, change("platform_secret", key, ChangeAction.CREATE, {"secret": Secret("c")})
    )
    await apply(engine, BOX, change("platform_secret", key, ChangeAction.DELETE))
    assert (await read(engine)).children["secrets"] == ()


async def test_a_secret_cannot_be_written_without_a_settings_key(engine: AsyncEngine) -> None:
    with pytest.raises(DomainError) as refused:
        await apply(
            engine,
            None,
            change(
                "platform_secret", ("smtp_password",), ChangeAction.CREATE, {"secret": Secret("x")}
            ),
        )

    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY


async def test_a_secret_that_is_not_a_secret_or_a_service_app_that_is_not_json_is_refused(
    engine: AsyncEngine,
) -> None:
    for key, value in (
        (("smtp_password",), "plain"),
        (("webex_service_app",), Secret("not json")),
        (("webex_service_app",), Secret("[1]")),
    ):
        with pytest.raises(DomainError) as refused:
            await apply(
                engine, BOX, change("platform_secret", key, ChangeAction.CREATE, {"secret": value})
            )
        assert refused.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_the_api_switches_are_written_listed_fields_only(engine: AsyncEngine) -> None:
    await apply(
        engine,
        BOX,
        change("api_settings", (), ChangeAction.CREATE, {"enabled": True, "docs_public": True}),
    )
    await apply(
        engine,
        BOX,
        change(
            "api_settings",
            (),
            ChangeAction.UPDATE,
            {"enabled": False, "docs_public": False},
            fields=("enabled",),
        ),
    )

    assert (await read(engine)).children["api"][0].fields == {"enabled": False, "docs_public": True}
    await apply(
        engine,
        BOX,
        change(
            "api_settings", (), ChangeAction.UPDATE, {"docs_public": False}, fields=("docs_public",)
        ),
    )
    async with AsyncSession(engine) as session:
        assert (await session.get(ApiSettingsRow, 1)) is not None
    await apply(engine, BOX, change("api_settings", (), ChangeAction.DELETE))
    async with AsyncSession(engine) as session:
        assert (await session.get(ApiSettingsRow, 1)) is None


DESTINATION: dict[str, Value] = {
    "kind": "http",
    "address": "https://logs.voorbeeld.nl/in",
    "index_name": "",
    "source": "",
    "min_level": "info",
    "categories": "audit",
    "include_diagnostic": False,
    "include_trace": False,
    "enabled": False,
    "ca_pem": "",
}


async def test_a_log_destination_seals_its_credential_under_its_id(engine: AsyncEngine) -> None:
    await apply(
        engine,
        BOX,
        change(
            "log_destination",
            ("web",),
            ChangeAction.CREATE,
            {**DESTINATION, "secret": Secret("tok")},
        ),
    )

    async with AsyncSession(engine) as session:
        row = await session.scalar(select(LogDestinationRow))
    assert row is not None
    assert BOX.open(secret_name(row.id), row.secret_nonce, row.secret_ciphertext) == b"tok"  # type: ignore[arg-type]
    assert row.created_at is not None
    assert row.updated_at is not None


async def test_a_log_destination_changes_only_what_is_listed_and_forgets_its_test(
    engine: AsyncEngine,
) -> None:
    key = ("web",)
    await apply(engine, BOX, change("log_destination", key, ChangeAction.CREATE, DESTINATION))
    async with AsyncSession(engine) as session, session.begin():
        row = await session.scalar(select(LogDestinationRow))
        assert row is not None
        row.tested_at = NOW

    await apply(engine, BOX, change("log_destination", key, ChangeAction.UPDATE, {"enabled": True}))
    async with AsyncSession(engine) as session:
        row = await session.scalar(select(LogDestinationRow))
    assert row is not None
    assert (row.enabled, row.address, row.tested_at is not None) == (
        True,
        DESTINATION["address"],
        True,
    )

    await apply(
        engine,
        BOX,
        change(
            "log_destination", key, ChangeAction.UPDATE, {"address": "https://other.voorbeeld.nl"}
        ),
    )
    async with AsyncSession(engine) as session:
        row = await session.scalar(select(LogDestinationRow))
    assert row is not None
    assert (row.address, row.tested_at) == ("https://other.voorbeeld.nl", None)


async def test_a_log_credential_is_replaced_removed_and_needs_a_key(engine: AsyncEngine) -> None:
    key = ("web",)
    await apply(
        engine,
        BOX,
        change("log_destination", key, ChangeAction.CREATE, {**DESTINATION, "secret": Secret("a")}),
    )
    await apply(
        engine, BOX, change("log_destination", key, ChangeAction.UPDATE, {"secret": Secret("b")})
    )
    assert children(await read(engine), "log_destinations")[key].fields["secret"] == Secret("b")

    with pytest.raises(DomainError) as refused:
        await apply(
            engine,
            None,
            change("log_destination", key, ChangeAction.UPDATE, {"secret": Secret("c")}),
        )
    assert refused.value.code is ErrorCode.INTEGRATION_NOT_READY
    with pytest.raises(DomainError):
        await apply(
            engine, BOX, change("log_destination", key, ChangeAction.UPDATE, {"secret": "plain"})
        )

    await apply(engine, BOX, change("log_destination", key, ChangeAction.UPDATE, {"secret": None}))
    assert "secret" not in children(await read(engine), "log_destinations")[key].fields


async def test_deleting_a_log_destination_removes_its_status(engine: AsyncEngine) -> None:
    key = ("web",)
    await apply(engine, BOX, change("log_destination", key, ChangeAction.CREATE, DESTINATION))
    async with AsyncSession(engine) as session, session.begin():
        row = await session.scalar(select(LogDestinationRow))
        assert row is not None
        session.add(
            LogDestinationStatusRow(
                destination_id=row.id, service="web", state="ok", updated_at=NOW
            )
        )

    await apply(engine, BOX, change("log_destination", key, ChangeAction.DELETE))

    async with AsyncSession(engine) as session:
        assert (await session.scalar(select(LogDestinationStatusRow))) is None
        assert (await session.scalar(select(LogDestinationRow))) is None
    with pytest.raises(DomainError) as missing:
        await apply(engine, BOX, change("log_destination", key, ChangeAction.DELETE))
    assert missing.value.code is ErrorCode.LOG_DESTINATION_NOT_FOUND


ROLE: dict[str, Value] = {
    "template": "org_admin",
    "name_nl": "Planner",
    "name_en": "Planner",
    "permissions": ["dashboard.view", "audit.view"],
}


async def test_a_role_is_created_changed_with_a_version_and_deleted(engine: AsyncEngine) -> None:
    key = ("planner",)
    await apply(engine, BOX, change("role", key, ChangeAction.CREATE, ROLE))
    await apply(engine, BOX, change("role", key, ChangeAction.UPDATE, {"name_en": "Scheduler"}))
    await apply(
        engine, BOX, change("role", key, ChangeAction.UPDATE, {"permissions": ["audit.view"]})
    )

    async with AsyncSession(engine) as session:
        row = await session.scalar(select(RoleRow))
    assert row is not None
    assert (row.version, row.name_en, row.name_nl, row.permissions) == (
        3,
        "Scheduler",
        "Planner",
        "audit.view",
    )
    await apply(engine, BOX, change("role", key, ChangeAction.DELETE))
    assert (await read(engine)).children["roles"] == ()
    with pytest.raises(DomainError) as missing:
        await apply(engine, BOX, change("role", key, ChangeAction.UPDATE, {"name_en": "x"}))
    assert missing.value.code is ErrorCode.ROLE_NOT_FOUND


async def test_role_permissions_must_be_a_list(engine: AsyncEngine) -> None:
    with pytest.raises(DomainError):
        await apply(
            engine,
            BOX,
            change("role", ("p",), ChangeAction.CREATE, {**ROLE, "permissions": "audit.view"}),
        )


async def test_a_calendar_and_its_days_are_written_renamed_and_deleted(engine: AsyncEngine) -> None:
    await apply(
        engine, BOX, change("holiday_calendar", ("nl",), ChangeAction.CREATE, {"name": "NL"})
    )
    day = ("nl", "2026-12-25")
    await apply(
        engine,
        BOX,
        change("holiday", ("2026-12-25",), ChangeAction.CREATE, {"name": "Kerst"}, address=day),
    )
    await apply(
        engine,
        BOX,
        change(
            "holiday",
            ("2026-12-25",),
            ChangeAction.UPDATE,
            {"name": "Eerste kerstdag"},
            address=day,
        ),
    )
    await apply(
        engine, BOX, change("holiday_calendar", ("nl",), ChangeAction.UPDATE, {"name": "Nederland"})
    )

    calendar = children(await read(engine), "holiday_calendars")[("nl",)]
    assert calendar.fields == {"name": "Nederland"}
    assert calendar.children["days"][0].fields == {"name": "Eerste kerstdag"}

    await apply(engine, BOX, change("holiday", ("2026-12-25",), ChangeAction.DELETE, address=day))
    await apply(engine, BOX, change("holiday", ("2026-12-25",), ChangeAction.DELETE, address=day))
    assert children(await read(engine), "holiday_calendars")[("nl",)].children["days"] == ()

    await apply(
        engine,
        BOX,
        change(
            "holiday",
            ("2026-12-26",),
            ChangeAction.CREATE,
            {"name": "Tweede"},
            address=("nl", "2026-12-26"),
        ),
    )
    await apply(engine, BOX, change("holiday_calendar", ("nl",), ChangeAction.DELETE))
    async with AsyncSession(engine) as session:
        assert (await session.scalar(select(HolidayCalendarRow))) is None
        assert (await session.scalar(select(HolidayRow))) is None
    with pytest.raises(DomainError) as missing:
        await apply(
            engine,
            BOX,
            change(
                "holiday",
                ("2026-12-26",),
                ChangeAction.CREATE,
                {"name": "x"},
                address=("nl", "2026-12-26"),
            ),
        )
    assert missing.value.code is ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND
    assert date.fromisoformat("2026-12-26").year == 2026
