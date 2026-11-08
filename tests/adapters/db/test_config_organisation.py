"""Reading and writing the organisation core of a configuration document (ADR 0099 step 2)."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_organisation import WRITERS, read_organisations
from app.adapters.db.contact_json import contacts_json
from app.adapters.db.models import (
    ApiClientGroupRow,
    ApiClientRow,
    GroupRow,
    HolidayCalendarRow,
    IntegrationCredentialRow,
    OrganisationContactsRow,
    OrganisationFontRow,
    OrganisationHolidayCalendarRow,
    OrganisationLogoRow,
    OrganisationOriginRow,
    OrganisationRow,
    OrganisationSettingRow,
    OrganisationTextRow,
    SlotRow,
)
from app.domain.branding import FontFile, FontRole, Logo
from app.domain.configuration_changes import Change, ChangeAction, RuleContext, Usage
from app.domain.configuration_document import Document, Mode, Scope, parse_document, to_json
from app.domain.configuration_plan import plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS, V1
from app.domain.configuration_values import Binary, Value
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.errors import DomainError, ErrorCode
from tests.fakes.woff2 import make_woff2
from tests.support.config_changes import planned
from tests.support.configuration import allow
from tests.support.images import png

PNG = png(metadata=False)
FONT = make_woff2(1)
OTHER_FONT = make_woff2(2)
BLOCK = ContactBlock((ContactOption.create(ContactKind.PHONE, "030 123 4567"),), {"nl": "Bel ons"})


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine) as opened, opened.begin():
        yield opened


async def seed(session: AsyncSession) -> None:
    """One organisation with every kind of child, one bare organisation, two calendars."""
    logo = Logo.from_upload(PNG)
    font = FontFile(FontRole.TEXT, FONT)
    acme = OrganisationRow(
        name="Acme",
        slug="acme",
        idp_key="acme-idp",
        default_locale="en",
        display_name="Acme BV",
        accent_color="#01689B",
        theme_tokens=json.dumps({"color-text": "#111111"}),
        header_style="accent",
        logo_media_type=logo.media_type,
        logo_digest=logo.digest,
        font_digests=json.dumps({"text": font.digest}),
    )
    bare = OrganisationRow(name="Bare", slug="bare", idp_key="bare")
    session.add_all([acme, bare, HolidayCalendarRow(slug="nl", name="NL")])
    session.add(HolidayCalendarRow(slug="be", name="BE"))
    await session.flush()
    session.add_all(
        [
            OrganisationContactsRow(organisation_id=acme.id, body=contacts_json(BLOCK)),
            OrganisationOriginRow(organisation_id=acme.id, origin="https://b.example", position=1),
            OrganisationOriginRow(organisation_id=acme.id, origin="https://a.example", position=0),
            OrganisationSettingRow(
                organisation_id=acme.id,
                key="staff_dormant_after_days",
                value="30",
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            OrganisationTextRow(
                organisation_id=acme.id, locale="nl", key="message.signoff", text="Tot ziens"
            ),
            OrganisationLogoRow(organisation_id=acme.id, data=logo.data),
            OrganisationFontRow(organisation_id=acme.id, role="text", data=font.data),
        ]
    )
    calendar = await session.scalar(
        select(HolidayCalendarRow.id).where(HolidayCalendarRow.slug == "nl")
    )
    session.add(
        OrganisationHolidayCalendarRow(organisation_id=acme.id, calendar_id=int(calendar or 0))
    )
    await session.flush()


def change(
    kind: str,
    action: ChangeAction,
    key: tuple[str, ...],
    address: tuple[str, ...],
    values: dict[str, Value],
    fields: tuple[str, ...] = (),
) -> Change:
    return planned(kind, address, action, values, fields, key=key)


async def apply(session: AsyncSession, *changes: Change) -> None:
    for item in changes:
        await WRITERS[item.kind](session, None, item)
    await session.flush()


def installation(read: dict[str, Entity]) -> Entity:
    return Entity(
        "installation",
        (),
        children={"platform": (Entity("platform"),), "organisations": tuple(read.values())},
    )


async def test_the_reader_states_an_organisation_as_the_kinds_expect(session: AsyncSession) -> None:
    await seed(session)

    read = await read_organisations(session, None)

    assert set(read) == {"acme", "bare"}
    acme = read["acme"]
    assert acme.fields == {
        "name": "Acme",
        "idp_key": "acme-idp",
        "default_locale": "en",
        "display_name": "Acme BV",
        "accent_color": "#01689B",
        "header_style": "accent",
        "theme_tokens": {"color-text": "#111111"},
        "contacts": {
            "headings": {"nl": "Bel ons"},
            "options": [{"kind": "phone", "value": "030 123 4567", "labels": {}, "hours": {}}],
        },
        "origins": ["https://a.example", "https://b.example"],
        "holiday_calendars": ["nl"],
    }
    assert [(e.key, e.fields) for e in acme.children["settings"]] == [
        (("staff_dormant_after_days",), {"value": 30})
    ]
    assert [(e.key, e.fields) for e in acme.children["texts"]] == [
        (("nl", "message.signoff"), {"text": "Tot ziens"})
    ]
    (logo,) = acme.children["logo"]
    assert logo.fields == {"data": Binary("image/png", PNG)}
    assert [(e.key, e.fields) for e in acme.children["fonts"]] == [
        (("text",), {"data": Binary("font/woff2", FONT)})
    ]


async def test_a_bare_organisation_has_defaults_and_no_children(session: AsyncSession) -> None:
    await seed(session)

    bare = (await read_organisations(session, None))["bare"]

    assert bare.fields["idp_key"] is None
    assert bare.fields["contacts"] is None
    assert bare.fields["origins"] == []
    assert bare.fields["holiday_calendars"] == []
    assert "logo" not in bare.children
    assert bare.children["fonts"] == ()


async def test_an_idp_key_equal_to_the_slug_is_stated_as_none(session: AsyncSession) -> None:
    session.add(OrganisationRow(name="Same", slug="same", idp_key="same"))
    await session.flush()

    assert (await read_organisations(session, None))["same"].fields["idp_key"] is None


async def test_what_is_read_planned_against_itself_changes_nothing(session: AsyncSession) -> None:
    await seed(session)
    current = installation(await read_organisations(session, None))
    document = parse_document(
        to_json(Document(V1.name, Mode.MERGE, Scope(), current, "", V1)),
        FORMATS,
    )

    result = plan_document(
        document,
        current,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(),
        settings_version=1,
    )

    assert result.refusals == ()
    assert result.changes == ()


async def test_an_organisation_is_created_with_its_rows(session: AsyncSession) -> None:
    values: dict[str, Value] = {
        "name": "New",
        "idp_key": None,
        "default_locale": "nl",
        "display_name": None,
        "accent_color": "#01689B",
        "header_style": "standard",
        "theme_tokens": {"color-text": "#222222"},
        "contacts": {
            "headings": {},
            "options": [{"kind": "email", "value": "a@b.nl", "labels": {}, "hours": {}}],
        },
        "origins": ["https://x.example", "https://y.example"],
        "holiday_calendars": ["nl", "be"],
    }
    await seed(session)

    await apply(session, change("organisation", ChangeAction.CREATE, ("new",), ("new",), values))

    new = (await read_organisations(session, None))["new"]
    assert new.fields["idp_key"] is None  # stored as the slug
    assert new.fields["theme_tokens"] == {"color-text": "#222222"}
    assert new.fields["origins"] == ["https://x.example", "https://y.example"]
    assert new.fields["holiday_calendars"] == ["be", "nl"]
    assert new.fields["contacts"] is not None
    stored = await session.scalar(
        select(OrganisationRow.idp_key).where(OrganisationRow.slug == "new")
    )
    assert stored == "new"


async def test_an_update_writes_only_the_listed_fields(session: AsyncSession) -> None:
    await seed(session)
    before = (await read_organisations(session, None))["acme"].fields
    values: dict[str, Value] = {
        **before,
        "name": "Acme Two",
        "origins": ["https://z.example"],
        "default_locale": "nl",
    }

    await apply(
        session,
        change(
            "organisation", ChangeAction.UPDATE, ("acme",), ("acme",), values, ("name", "origins")
        ),
    )

    after = (await read_organisations(session, None))["acme"].fields
    assert after == {**before, "name": "Acme Two", "origins": ["https://z.example"]}


async def test_an_update_can_clear_and_replace_the_rows_it_owns(session: AsyncSession) -> None:
    await seed(session)
    before = (await read_organisations(session, None))["acme"].fields
    values: dict[str, Value] = {
        **before,
        "contacts": None,
        "holiday_calendars": ["be"],
        "idp_key": None,
        "display_name": None,
        "theme_tokens": {},
    }

    await apply(
        session,
        change(
            "organisation",
            ChangeAction.UPDATE,
            ("acme",),
            ("acme",),
            values,
            ("contacts", "holiday_calendars", "idp_key", "display_name", "theme_tokens"),
        ),
    )

    after = (await read_organisations(session, None))["acme"].fields
    assert after["contacts"] is None
    assert after["holiday_calendars"] == ["be"]
    assert after["idp_key"] is None
    assert after["display_name"] is None
    assert after["theme_tokens"] == {}
    assert await session.scalar(select(func.count(OrganisationContactsRow.organisation_id))) == 0


async def test_a_calendar_that_does_not_exist_is_refused(session: AsyncSession) -> None:
    await seed(session)
    values: dict[str, Value] = {"holiday_calendars": ["nowhere"]}

    with pytest.raises(DomainError) as refused:
        await apply(
            session,
            change(
                "organisation",
                ChangeAction.UPDATE,
                ("acme",),
                ("acme",),
                values,
                ("holiday_calendars",),
            ),
        )

    assert refused.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_a_change_below_an_unknown_organisation_is_refused(session: AsyncSession) -> None:
    with pytest.raises(DomainError) as refused:
        await apply(
            session,
            change(
                "organisation_setting", ChangeAction.CREATE, ("x",), ("ghost", "x"), {"value": 1}
            ),
        )

    assert refused.value.field == "organisation"


async def test_a_delete_takes_groups_slots_clients_and_the_bot_token_with_it(
    session: AsyncSession,
) -> None:
    await seed(session)
    acme = await session.scalar(select(OrganisationRow.id).where(OrganisationRow.slug == "acme"))
    group = GroupRow(
        organisation_id=acme,
        name="G",
        slug="g",
        timezone="Europe/Amsterdam",
        slot_minutes=30,
        min_notice_minutes=60,
        horizon_days=60,
        phone_mode="optional",
    )
    session.add(group)
    await session.flush()
    session.add(
        SlotRow(
            group_id=group.id,
            start=datetime(2030, 1, 1, tzinfo=UTC),
            end=datetime(2030, 1, 1, 0, 30, tzinfo=UTC),
            capacity_snapshot=1,
        )
    )
    client = ApiClientRow(
        client_id="opc_x",
        name="Bot",
        organisation_id=acme,
        scopes="agent",
        enabled=False,
        secret_hash="0" * 64,
        secret_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(client)
    await session.flush()
    session.add(ApiClientGroupRow(client_pk=client.id, group_id=group.id))
    session.add(
        IntegrationCredentialRow(
            name=f"webex-bot:{acme}", nonce=b"n", ciphertext=b"c", updated_at=datetime.now(UTC)
        )
    )
    await session.flush()

    await apply(session, change("organisation", ChangeAction.DELETE, ("acme",), ("acme",), {}))

    assert set(await read_organisations(session, None)) == {"bare"}
    for model in (GroupRow, SlotRow, ApiClientRow, IntegrationCredentialRow, OrganisationOriginRow):
        assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_settings_are_set_replaced_and_removed(session: AsyncSession) -> None:
    await seed(session)
    address = ("acme", "time_format")

    await apply(
        session,
        change(
            "organisation_setting", ChangeAction.CREATE, ("time_format",), address, {"value": "12h"}
        ),
    )
    await apply(
        session,
        change(
            "organisation_setting",
            ChangeAction.UPDATE,
            ("time_format",),
            address,
            {"value": "24h"},
            ("value",),
        ),
    )
    values = {
        e.key[0]: e.fields["value"]
        for e in (await read_organisations(session, None))["acme"].children["settings"]
    }
    assert values == {"staff_dormant_after_days": 30, "time_format": "24h"}

    await apply(
        session, change("organisation_setting", ChangeAction.DELETE, ("time_format",), address, {})
    )
    keys = [
        e.key[0] for e in (await read_organisations(session, None))["acme"].children["settings"]
    ]
    assert keys == ["staff_dormant_after_days"]
    stamp = await session.scalar(
        select(OrganisationSettingRow.updated_at).where(
            OrganisationSettingRow.key == "staff_dormant_after_days"
        )
    )
    assert stamp is not None


async def test_texts_are_set_replaced_and_removed(session: AsyncSession) -> None:
    await seed(session)
    address = ("acme", "en", "message.signoff")

    await apply(
        session,
        change(
            "organisation_text",
            ChangeAction.CREATE,
            ("en", "message.signoff"),
            address,
            {"text": "Bye"},
        ),
    )
    await apply(
        session,
        change(
            "organisation_text",
            ChangeAction.UPDATE,
            ("en", "message.signoff"),
            address,
            {"text": "Goodbye"},
            ("text",),
        ),
    )
    texts = {
        e.key: e.fields["text"]
        for e in (await read_organisations(session, None))["acme"].children["texts"]
    }
    assert texts == {("en", "message.signoff"): "Goodbye", ("nl", "message.signoff"): "Tot ziens"}

    await apply(
        session,
        change("organisation_text", ChangeAction.DELETE, ("en", "message.signoff"), address, {}),
    )
    assert len((await read_organisations(session, None))["acme"].children["texts"]) == 1


async def test_a_logo_is_replaced_removed_and_its_digest_follows(session: AsyncSession) -> None:
    await seed(session)
    new = Binary("image/png", png(metadata=False) + b"")
    other = Binary("image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 20)

    await apply(
        session,
        change("organisation_logo", ChangeAction.UPDATE, (), ("acme",), {"data": new}, ("data",)),
    )
    row = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == "acme"))
    assert row is not None
    assert (row.logo_media_type, row.logo_digest) == (
        "image/png",
        Logo("image/png", new.data).digest,
    )

    await apply(session, change("organisation_logo", ChangeAction.DELETE, (), ("acme",), {}))
    await session.refresh(row)
    assert (row.logo_media_type, row.logo_digest) == (None, None)
    assert "logo" not in (await read_organisations(session, None))["acme"].children

    await apply(
        session, change("organisation_logo", ChangeAction.CREATE, (), ("acme",), {"data": other})
    )
    await session.refresh(row)
    assert row.logo_media_type == "image/jpeg"
    assert other.data in {r.data for r in await session.scalars(select(OrganisationLogoRow))}


async def test_a_logo_without_a_file_is_refused(session: AsyncSession) -> None:
    await seed(session)

    with pytest.raises(DomainError):
        await apply(
            session,
            change("organisation_logo", ChangeAction.CREATE, (), ("acme",), {"data": "text"}),
        )


async def test_fonts_are_set_replaced_and_removed_with_their_digests(session: AsyncSession) -> None:
    await seed(session)
    row = await session.scalar(select(OrganisationRow).where(OrganisationRow.slug == "acme"))
    assert row is not None

    await apply(
        session,
        change(
            "organisation_font",
            ChangeAction.CREATE,
            ("heading",),
            ("acme", "heading"),
            {"data": Binary("font/woff2", OTHER_FONT)},
        ),
    )
    await session.refresh(row)
    assert json.loads(row.font_digests) == {
        "heading": FontFile(FontRole.HEADING, OTHER_FONT).digest,
        "text": FontFile(FontRole.TEXT, FONT).digest,
    }

    await apply(
        session,
        change(
            "organisation_font",
            ChangeAction.UPDATE,
            ("text",),
            ("acme", "text"),
            {"data": Binary("font/woff2", OTHER_FONT)},
            ("data",),
        ),
    )
    await session.refresh(row)
    assert json.loads(row.font_digests)["text"] == FontFile(FontRole.TEXT, OTHER_FONT).digest

    await apply(
        session,
        change("organisation_font", ChangeAction.DELETE, ("heading",), ("acme", "heading"), {}),
    )
    await session.refresh(row)
    assert set(json.loads(row.font_digests)) == {"text"}
    fonts = (await read_organisations(session, None))["acme"].children["fonts"]
    assert [e.key for e in fonts] == [("text",)]
