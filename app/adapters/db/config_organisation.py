"""Reading and writing the organisation core of a configuration document (ADR 0099 step 2).

``read_organisations`` builds the current tree of every organisation in the form the v1 kinds
expect (``app.domain.configuration_v1_organisation``): its own fields, its class D settings, its
own texts, its logo and its fonts. The other children of an organisation (channels, identity
providers, closure scenarios, groups, alert targets) are attached by the assembler.

``WRITERS`` apply one ``Change`` of a plan inside the caller's transaction. They write
the same rows the admin screens write (and compute the same digests), never bump the settings
version and never audit: the apply does both once. Raw SQL is not used; the models are.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.contact_json import contacts_block, contacts_json
from app.adapters.db.models import (
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
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_names import (
    ORGANISATION_FONT_KIND,
    ORGANISATION_KIND,
    ORGANISATION_LOGO_KIND,
    ORGANISATION_SETTING_KIND,
    ORGANISATION_TEXT_KIND,
)
from app.domain.configuration_values import Binary, Json, Value
from app.domain.contacts import block_to_json
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import recognise

type Writer = Callable[[AsyncSession, SecretBox | None, Change], Awaitable[None]]
"""Applies one change of a plan inside the caller's transaction."""

FONT_MEDIA_TYPE = "font/woff2"
"""The media type a document gives every font (the format only holds WOFF2)."""


# --- reading -------------------------------------------------------------------------------


async def read_organisations(session: AsyncSession, box: SecretBox | None) -> dict[str, Entity]:
    """Return every organisation, by slug, with its own fields and its own children.

    Args:
        session: The open session.
        box: Unused by this slice (it holds no secret); kept so every reader has one shape.
    """
    organisations = list(
        await session.scalars(select(OrganisationRow).order_by(OrganisationRow.id))
    )
    contacts = {
        row.organisation_id: row.body
        for row in await session.scalars(select(OrganisationContactsRow))
    }
    origins: dict[int, list[str]] = {}
    for origin in await session.scalars(
        select(OrganisationOriginRow).order_by(
            OrganisationOriginRow.organisation_id, OrganisationOriginRow.position
        )
    ):
        origins.setdefault(origin.organisation_id, []).append(origin.origin)
    calendars: dict[int, list[str]] = {}
    for owner, slug in await session.execute(
        select(OrganisationHolidayCalendarRow.organisation_id, HolidayCalendarRow.slug)
        .join(
            HolidayCalendarRow,
            HolidayCalendarRow.id == OrganisationHolidayCalendarRow.calendar_id,
        )
        .order_by(HolidayCalendarRow.slug)
    ):
        calendars.setdefault(owner, []).append(slug)
    settings = await _settings(session)
    texts = await _texts(session)
    logos = {
        row.organisation_id: row.data for row in await session.scalars(select(OrganisationLogoRow))
    }
    fonts: dict[int, list[OrganisationFontRow]] = {}
    for font in await session.scalars(
        select(OrganisationFontRow).order_by(
            OrganisationFontRow.organisation_id, OrganisationFontRow.role
        )
    ):
        fonts.setdefault(font.organisation_id, []).append(font)
    return {
        row.slug: Entity(
            ORGANISATION_KIND,
            (row.slug,),
            fields=_fields(
                row, contacts.get(row.id), origins.get(row.id, []), calendars.get(row.id, [])
            ),
            children=_children(row, settings, texts, logos, fonts),
        )
        for row in organisations
    }


def _children(
    row: OrganisationRow,
    settings: dict[int, tuple[Entity, ...]],
    texts: dict[int, tuple[Entity, ...]],
    logos: dict[int, bytes],
    fonts: dict[int, list[OrganisationFontRow]],
) -> dict[str, tuple[Entity, ...]]:
    """Return what an organisation holds itself; a single logo that is not there is left out."""
    children: dict[str, tuple[Entity, ...]] = {
        "settings": settings.get(row.id, ()),
        "texts": texts.get(row.id, ()),
        "fonts": tuple(
            Entity(
                ORGANISATION_FONT_KIND,
                (font.role,),
                fields={"data": Binary(FONT_MEDIA_TYPE, font.data)},
            )
            for font in fonts.get(row.id, ())
        ),
    }
    logo = _logo(row, logos.get(row.id))
    if logo:
        children["logo"] = logo
    return children


def _fields(
    row: OrganisationRow, contacts: str | None, origins: list[str], calendars: list[str]
) -> dict[str, Value]:
    """Return the fields of an organisation as the kind states them."""
    return {
        "name": row.name,
        "idp_key": None if row.idp_key == row.slug else row.idp_key,
        "default_locale": row.default_locale,
        "display_name": row.display_name,
        "accent_color": row.accent_color,
        "header_style": row.header_style,
        "theme_tokens": json.loads(row.theme_tokens or "{}"),
        "contacts": _contacts(contacts),
        "origins": list(origins),
        "holiday_calendars": list(calendars),
    }


def _contacts(body: str | None) -> Json:
    """Return the stored contact block in its normalised form; an empty block is none."""
    if body is None:
        return None
    block = contacts_block(body)
    return None if block.is_empty else block_to_json(block)  # type: ignore[return-value]


def _logo(row: OrganisationRow, data: bytes | None) -> tuple[Entity, ...]:
    """Return the logo as a single object, or none."""
    if data is None:
        return ()
    media_type = row.logo_media_type or recognise(data) or "application/octet-stream"
    return (Entity(ORGANISATION_LOGO_KIND, (), fields={"data": Binary(media_type, data)}),)


async def _settings(session: AsyncSession) -> dict[int, tuple[Entity, ...]]:
    """Return every organisation's class D values as objects, by organisation id."""
    found: dict[int, list[Entity]] = {}
    for row in await session.scalars(
        select(OrganisationSettingRow).order_by(
            OrganisationSettingRow.organisation_id, OrganisationSettingRow.key
        )
    ):
        found.setdefault(row.organisation_id, []).append(
            Entity(ORGANISATION_SETTING_KIND, (row.key,), fields={"value": json.loads(row.value)})
        )
    return {owner: tuple(entities) for owner, entities in found.items()}


async def _texts(session: AsyncSession) -> dict[int, tuple[Entity, ...]]:
    """Return every organisation's own texts as objects, by organisation id."""
    found: dict[int, list[Entity]] = {}
    for row in await session.scalars(
        select(OrganisationTextRow).order_by(
            OrganisationTextRow.organisation_id, OrganisationTextRow.locale, OrganisationTextRow.key
        )
    ):
        found.setdefault(row.organisation_id, []).append(
            Entity(ORGANISATION_TEXT_KIND, (row.locale, row.key), fields={"text": row.text})
        )
    return {owner: tuple(entities) for owner, entities in found.items()}


# --- writing -------------------------------------------------------------------------------


def _missing(field: str) -> DomainError:
    """Return the refusal for a parent that does not exist."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


async def _organisation_id(session: AsyncSession, slug: str) -> int:
    """Return the id of the organisation ``slug``; refuse when there is none."""
    found = await session.scalar(select(OrganisationRow.id).where(OrganisationRow.slug == slug))
    if found is None:
        raise _missing("organisation")
    return int(found)


def _text(value: Value) -> str:
    """Return a field value that the kind checked to be a text."""
    return str(value)


async def _write_organisation(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create, update or delete an organisation with the rows that belong to it."""
    slug = change.key[0]
    if change.action is ChangeAction.DELETE:
        await _delete_organisation(session, await _organisation_id(session, slug))
        return
    names = set(change.values) if change.action is ChangeAction.CREATE else set(change.fields)
    values = change.values
    if change.action is ChangeAction.CREATE:
        row = OrganisationRow(
            name=_text(values["name"]),
            slug=slug,
            idp_key=_text(values["idp_key"]) if values.get("idp_key") else slug,
            default_locale=_text(values["default_locale"]),
            display_name=_optional(values.get("display_name")),
            accent_color=_text(values["accent_color"]),
            header_style=_text(values["header_style"]),
            theme_tokens=_tokens(values.get("theme_tokens")),
        )
        session.add(row)
        await session.flush()
        organisation_id = row.id
    else:
        organisation_id = await _organisation_id(session, slug)
        columns = _columns(names, values, slug)
        if columns:
            await session.execute(
                update(OrganisationRow)
                .where(OrganisationRow.id == organisation_id)
                .values(**columns)
            )
    if "contacts" in names:
        await _write_contacts(session, organisation_id, values.get("contacts"))
    if "origins" in names:
        await _write_origins(session, organisation_id, values.get("origins"))
    if "holiday_calendars" in names:
        await _write_calendars(session, organisation_id, values.get("holiday_calendars"))


def _optional(value: Value) -> str | None:
    """Return a text field that may be none."""
    return None if value is None else str(value)


def _tokens(value: Value) -> str:
    """Return theme tokens as the portal stores them: JSON with sorted keys."""
    return json.dumps(dict(value) if isinstance(value, dict) else {}, sort_keys=True)


def _columns(names: set[str], given: Mapping[str, Value], slug: str) -> dict[str, object]:
    """Return the organisation columns an update writes, by the field names it lists."""
    columns: dict[str, object] = {}
    for name in ("name", "default_locale", "accent_color", "header_style"):
        if name in names:
            columns[name] = _text(given[name])
    if "idp_key" in names:
        columns["idp_key"] = _text(given["idp_key"]) if given.get("idp_key") else slug
    if "display_name" in names:
        columns["display_name"] = _optional(given.get("display_name"))
    if "theme_tokens" in names:
        columns["theme_tokens"] = _tokens(given.get("theme_tokens"))
    return columns


async def _write_contacts(session: AsyncSession, organisation_id: int, value: Value) -> None:
    """Replace the default contact block; none removes the row."""
    await session.execute(
        delete(OrganisationContactsRow).where(
            OrganisationContactsRow.organisation_id == organisation_id
        )
    )
    if value is not None:
        block = contacts_block(json.dumps(value))
        if not block.is_empty:
            session.add(
                OrganisationContactsRow(organisation_id=organisation_id, body=contacts_json(block))
            )
    await session.flush()


async def _write_origins(session: AsyncSession, organisation_id: int, value: Value) -> None:
    """Replace the embedding origins; the list order is the position."""
    await session.execute(
        delete(OrganisationOriginRow).where(
            OrganisationOriginRow.organisation_id == organisation_id
        )
    )
    session.add_all(
        OrganisationOriginRow(organisation_id=organisation_id, origin=str(origin), position=index)
        for index, origin in enumerate(value if isinstance(value, list) else [])
    )
    await session.flush()


async def _write_calendars(session: AsyncSession, organisation_id: int, value: Value) -> None:
    """Replace the chosen holiday calendars; a slug that names no calendar is refused."""
    slugs = [str(item) for item in (value if isinstance(value, list) else [])]
    await session.execute(
        delete(OrganisationHolidayCalendarRow).where(
            OrganisationHolidayCalendarRow.organisation_id == organisation_id
        )
    )
    ids = dict(
        (
            await session.execute(
                select(HolidayCalendarRow.slug, HolidayCalendarRow.id).where(
                    HolidayCalendarRow.slug.in_(slugs)
                )
            )
        ).all()
    )
    if len(ids) != len(set(slugs)):
        raise _missing("holiday_calendars")
    session.add_all(
        OrganisationHolidayCalendarRow(organisation_id=organisation_id, calendar_id=ids[slug])
        for slug in sorted(set(slugs))
    )
    await session.flush()


async def _delete_organisation(session: AsyncSession, organisation_id: int) -> None:
    """Erase an organisation as the portal's delete does, and what RESTRICT would stop.

    The planner refused an organisation something still refers to. What is left and restricts
    the row is removed here in the same transaction: its API clients (with their tokens and
    group links, by cascade) and its groups with their slots. The sealed Webex bot token has no
    foreign key and goes too, so a later organisation never inherits it by a reused id.
    """
    from app.adapters.db.uow import bot_credential_name  # noqa: PLC0415 - uow imports this slice

    group_ids = list(
        await session.scalars(
            select(GroupRow.id).where(GroupRow.organisation_id == organisation_id)
        )
    )
    await session.execute(
        delete(ApiClientRow).where(ApiClientRow.organisation_id == organisation_id)
    )
    if group_ids:
        await session.execute(delete(SlotRow).where(SlotRow.group_id.in_(group_ids)))
        await session.execute(delete(GroupRow).where(GroupRow.id.in_(group_ids)))
    await session.execute(
        delete(IntegrationCredentialRow).where(
            IntegrationCredentialRow.name == bot_credential_name(organisation_id)
        )
    )
    await session.execute(delete(OrganisationRow).where(OrganisationRow.id == organisation_id))


async def _write_setting(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Set or remove one class D value of an organisation."""
    organisation_id = await _organisation_id(session, change.address[0])
    key = change.key[0]
    if change.action is ChangeAction.DELETE:
        await session.execute(
            delete(OrganisationSettingRow).where(
                OrganisationSettingRow.organisation_id == organisation_id,
                OrganisationSettingRow.key == key,
            )
        )
        return
    await session.merge(
        OrganisationSettingRow(
            organisation_id=organisation_id,
            key=key,
            value=json.dumps(change.values["value"]),
            updated_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _write_text(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Set or remove the organisation's own text of one key in one locale."""
    organisation_id = await _organisation_id(session, change.address[0])
    locale, key = change.key
    if change.action is ChangeAction.DELETE:
        await session.execute(
            delete(OrganisationTextRow).where(
                OrganisationTextRow.organisation_id == organisation_id,
                OrganisationTextRow.locale == locale,
                OrganisationTextRow.key == key,
            )
        )
        return
    await session.merge(
        OrganisationTextRow(
            organisation_id=organisation_id,
            locale=locale,
            key=key,
            text=_text(change.values["text"]),
        )
    )
    await session.flush()


def _binary(change: Change) -> Binary:
    """Return the file a change writes."""
    data = change.values["data"]
    if not isinstance(data, Binary):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "data")
    return data


async def _write_logo(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Replace or remove the logo and keep its type and digest on the organisation."""
    organisation_id = await _organisation_id(session, change.address[0])
    logo = None if change.action is ChangeAction.DELETE else _logo_of(_binary(change))
    await session.execute(
        delete(OrganisationLogoRow).where(OrganisationLogoRow.organisation_id == organisation_id)
    )
    if logo is not None:
        session.add(OrganisationLogoRow(organisation_id=organisation_id, data=logo.data))
    await session.execute(
        update(OrganisationRow)
        .where(OrganisationRow.id == organisation_id)
        .values(
            logo_media_type=logo.media_type if logo else None,
            logo_digest=logo.digest if logo else None,
        )
    )


def _logo_of(binary: Binary) -> Logo:
    """Return the logo of a file the kind has already checked."""
    return Logo(binary.media_type, binary.data)


async def _write_font(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Replace or remove the font of one role and keep the digests on the organisation."""
    organisation_id = await _organisation_id(session, change.address[0])
    role = FontRole(change.key[0])
    font = None if change.action is ChangeAction.DELETE else FontFile(role, _binary(change).data)
    await session.execute(
        delete(OrganisationFontRow).where(
            OrganisationFontRow.organisation_id == organisation_id,
            OrganisationFontRow.role == role,
        )
    )
    if font is not None:
        session.add(OrganisationFontRow(organisation_id=organisation_id, role=role, data=font.data))
    stored = await session.scalar(
        select(OrganisationRow.font_digests).where(OrganisationRow.id == organisation_id)
    )
    digests: dict[str, str] = json.loads(stored or "{}")
    if font is None:
        digests.pop(role, None)
    else:
        digests[role] = font.digest
    await session.execute(
        update(OrganisationRow)
        .where(OrganisationRow.id == organisation_id)
        .values(font_digests=json.dumps(digests, sort_keys=True))
    )


WRITERS: dict[str, Writer] = {
    ORGANISATION_KIND: _write_organisation,
    ORGANISATION_SETTING_KIND: _write_setting,
    ORGANISATION_TEXT_KIND: _write_text,
    ORGANISATION_LOGO_KIND: _write_logo,
    ORGANISATION_FONT_KIND: _write_font,
}
"""The writer of each kind this slice owns, by v1 kind name."""
