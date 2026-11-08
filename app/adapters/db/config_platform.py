"""Reading and writing the platform aggregate of a configuration document (ADR 0099 step 2).

``read_platform`` builds the current ``platform`` object (settings, secrets, the API switch, log
destinations, custom roles, holiday calendars with their days) in the form the version-1 kinds
of ``app.domain.configuration_v1_platform`` expect; ``WRITERS`` apply one planned ``Change`` of
those kinds inside the caller's transaction. Secrets are opened with the ``SecretBox`` when
read and sealed again under the names the portal uses (ADR 0008). Neither side bumps the
settings version or writes an audit event: the caller does both once per apply.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.log_destinations import secret_name as log_secret_name
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
from app.domain.authorization import Permission
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_names import (
    API_SETTINGS_KIND,
    HOLIDAY_CALENDAR_KIND,
    HOLIDAY_KIND,
    LOG_DESTINATION_KIND,
    PLATFORM_KIND,
    PLATFORM_SECRET_KIND,
    PLATFORM_SETTING_KIND,
    ROLE_KIND,
)
from app.domain.configuration_v1_platform import SECRET_NAMES, WEBEX_SERVICE_APP, editable_setting
from app.domain.configuration_values import Json, Secret, Value
from app.domain.errors import DomainError, ErrorCode

type Writer = Callable[[AsyncSession, SecretBox | None, Change], Awaitable[None]]
"""Applies one planned change of one kind inside the caller's transaction."""

_SETTING_PREFIX = "setting:"  # noqa: S105  # nosec B105 - a record name prefix, not a secret
_API_SETTINGS_ID = 1
_PERMISSIONS = frozenset(p.value for p in Permission)


def _now() -> datetime:
    """Return the time stamped on the bookkeeping columns of a written row."""
    return datetime.now(UTC)


def _invalid(field: str) -> DomainError:
    """Return the refusal naming a field."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


def _need_box(box: SecretBox | None) -> SecretBox:
    """Return the box, or refuse: without a settings key no secret can be written."""
    if box is None:
        raise DomainError(ErrorCode.INTEGRATION_NOT_READY, "settings_key")
    return box


def _entity(kind: str, key: tuple[str, ...], fields: Mapping[str, Value]) -> Entity:
    """Return one object of the current configuration."""
    return Entity(kind, key, fields=dict(fields))


# --- reading ---------------------------------------------------------------------------------


async def read_platform(session: AsyncSession, box: SecretBox | None) -> Entity:
    """Return the current ``platform`` object with everything it holds."""
    children = {
        "settings": await _read_settings(session),
        "secrets": await _read_secrets(session, box),
        "api": (await _read_api(session),),
        "log_destinations": await _read_log_destinations(session, box),
        "roles": await _read_roles(session),
        "holiday_calendars": await _read_calendars(session),
    }
    return Entity(PLATFORM_KIND, (), children=children)


async def _read_settings(session: AsyncSession) -> tuple[Entity, ...]:
    """Return the stored portal values a document may hold, ordered by key."""
    rows = await session.scalars(select(PlatformSettingRow).order_by(PlatformSettingRow.key))
    found: list[Entity] = []
    for row in rows:
        if editable_setting(row.key) is None:
            continue
        try:
            value = json.loads(row.value)
        except ValueError:
            continue
        if isinstance(value, bool | int | float | str):
            found.append(_entity(PLATFORM_SETTING_KIND, (row.key,), {"value": value}))
    return tuple(found)


def _credential_text(name: str, plaintext: bytes) -> str | None:
    """Return the secret of a credential row as the document holds it, or ``None``."""
    try:
        opened = json.loads(plaintext)
    except ValueError:
        return None
    if name == WEBEX_SERVICE_APP:
        return json.dumps(opened, sort_keys=True) if isinstance(opened, dict) else None
    if isinstance(opened, dict) and isinstance(opened.get("value"), str):
        return str(opened["value"])
    return None


def _secret_key(record: str) -> str | None:
    """Return the document name of a credential row, or ``None`` if a document does not hold it."""
    if record == WEBEX_SERVICE_APP:
        return record
    key = record.removeprefix(_SETTING_PREFIX)
    held = record.startswith(_SETTING_PREFIX) and key in SECRET_NAMES and key != WEBEX_SERVICE_APP
    return key if held else None


async def _read_secrets(session: AsyncSession, box: SecretBox | None) -> tuple[Entity, ...]:
    """Return the sealed platform secrets that are set, opened; none without a settings key."""
    if box is None:
        return ()
    rows = await session.scalars(
        select(IntegrationCredentialRow).order_by(IntegrationCredentialRow.name)
    )
    found: list[Entity] = []
    for row in rows:
        key = _secret_key(row.name)
        if key is None:
            continue
        try:
            text = _credential_text(key, box.open(row.name, row.nonce, row.ciphertext))
        except Exception:  # noqa: S112  # nosec B112 - another key or a broken row: leave it out
            continue
        if text is not None:
            found.append(_entity(PLATFORM_SECRET_KIND, (key,), {"secret": Secret(text)}))
    return tuple(found)


async def _read_api(session: AsyncSession) -> Entity:
    """Return the machine API switches (both off when never saved)."""
    row = await session.get(ApiSettingsRow, _API_SETTINGS_ID)
    return _entity(
        API_SETTINGS_KIND,
        (),
        {
            "enabled": bool(row.enabled) if row else False,
            "docs_public": bool(row.docs_public) if row else False,
        },
    )


async def _read_log_destinations(
    session: AsyncSession, box: SecretBox | None
) -> tuple[Entity, ...]:
    """Return the log destinations, ordered by name; a set credential is opened."""
    rows = await session.scalars(select(LogDestinationRow).order_by(LogDestinationRow.name))
    found: list[Entity] = []
    for row in rows:
        fields: dict[str, Value] = {
            "kind": row.kind,
            "address": row.address,
            "index_name": row.index_name,
            "source": row.source,
            "min_level": row.min_level,
            "categories": row.categories,
            "include_diagnostic": row.include_diagnostic,
            "include_trace": row.include_trace,
            "enabled": row.enabled,
            "ca_pem": row.ca_pem,
        }
        if box is not None and row.secret_nonce is not None and row.secret_ciphertext is not None:
            try:
                plain = box.open(log_secret_name(row.id), row.secret_nonce, row.secret_ciphertext)
                fields["secret"] = Secret(plain.decode())
            except Exception:  # noqa: S110  # nosec B110 - another key: treat as not set
                pass
        found.append(_entity(LOG_DESTINATION_KIND, (row.name,), fields))
    return tuple(found)


async def _read_roles(session: AsyncSession) -> tuple[Entity, ...]:
    """Return the custom roles, ordered by claim value; unknown permissions are dropped."""
    rows = await session.scalars(select(RoleRow).order_by(RoleRow.claim_value))
    return tuple(
        _entity(
            ROLE_KIND,
            (row.claim_value,),
            {
                "template": row.template,
                "name_nl": row.name_nl,
                "name_en": row.name_en,
                "permissions": [*sorted(p for p in row.permissions.split() if p in _PERMISSIONS)],
            },
        )
        for row in rows
    )


async def _read_calendars(session: AsyncSession) -> tuple[Entity, ...]:
    """Return the holiday calendars with their days, ordered by slug and date."""
    calendars = (
        await session.scalars(select(HolidayCalendarRow).order_by(HolidayCalendarRow.slug))
    ).all()
    holidays = await session.scalars(select(HolidayRow).order_by(HolidayRow.day))
    days: dict[int, list[Entity]] = {}
    for holiday in holidays:
        days.setdefault(holiday.calendar_id, []).append(
            _entity(HOLIDAY_KIND, (holiday.day.isoformat(),), {"name": holiday.name})
        )
    return tuple(
        Entity(
            HOLIDAY_CALENDAR_KIND,
            (calendar.slug,),
            fields={"name": calendar.name},
            children={"days": tuple(days.get(calendar.id, ()))},
        )
        for calendar in calendars
    )


# --- writing ---------------------------------------------------------------------------------


def _json_scalar(value: Value, field: str) -> Json:
    """Return a plain JSON value of a change, refusing a secret or a file."""
    if isinstance(value, Secret) or not (
        value is None or isinstance(value, bool | int | float | str | list | dict)
    ):
        raise _invalid(field)
    return value


async def _write_setting(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Store, change or reset (delete = the default) the portal value of a setting."""
    key = change.key[0]
    if change.action is ChangeAction.DELETE:
        await session.execute(delete(PlatformSettingRow).where(PlatformSettingRow.key == key))
        return
    await session.merge(
        PlatformSettingRow(
            key=key,
            value=json.dumps(_json_scalar(change.values["value"], "value")),
            updated_at=_now(),
            expires_at=None,
        )
    )


def _credential_name(key: str) -> str:
    """Return the record name a platform secret is sealed under."""
    return key if key == WEBEX_SERVICE_APP else _SETTING_PREFIX + key


async def _write_secret(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Seal, replace or remove (delete, or an explicit null) a platform secret."""
    key = change.key[0]
    name = _credential_name(key)
    value = change.values.get("secret")
    if change.action is ChangeAction.DELETE or value is None:
        await session.execute(
            delete(IntegrationCredentialRow).where(IntegrationCredentialRow.name == name)
        )
        return
    if not isinstance(value, Secret):
        raise _invalid("secret")
    sealer = _need_box(box)
    if key == WEBEX_SERVICE_APP:
        try:
            payload = json.loads(value.reveal())
        except ValueError:
            raise _invalid("secret") from None
        if not isinstance(payload, dict):
            raise _invalid("secret")
    else:
        payload = {"value": value.reveal()}
    nonce, ciphertext = sealer.seal(name, json.dumps(payload).encode())
    await session.merge(
        IntegrationCredentialRow(name=name, nonce=nonce, ciphertext=ciphertext, updated_at=_now())
    )


async def _write_api(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Store the machine API switches in their single row, or remove it (all off)."""
    if change.action is ChangeAction.DELETE:
        await session.execute(delete(ApiSettingsRow).where(ApiSettingsRow.id == _API_SETTINGS_ID))
        return
    row = await session.get(ApiSettingsRow, _API_SETTINGS_ID)
    if row is None:
        row = ApiSettingsRow(id=_API_SETTINGS_ID, enabled=False, docs_public=False)
        session.add(row)
    names = _written(change)
    if "enabled" in names:
        row.enabled = bool(change.values["enabled"])
    if "docs_public" in names:
        row.docs_public = bool(change.values["docs_public"])
    row.updated_at = _now()


def _written(change: Change) -> tuple[str, ...]:
    """Return the names a change writes: all given ones on create, the listed ones on update."""
    if change.action is ChangeAction.CREATE:
        return tuple(change.values)
    return change.fields


_LOG_FIELDS = (
    "kind",
    "address",
    "index_name",
    "source",
    "min_level",
    "categories",
    "include_diagnostic",
    "include_trace",
    "enabled",
    "ca_pem",
)
_RESETS_TEST = frozenset({"kind", "address", "ca_pem", "secret"})


async def _log_row(session: AsyncSession, name: str) -> LogDestinationRow:
    """Return the destination called ``name``.

    Raises:
        DomainError: ``log_destination_not_found``.
    """
    row = await session.scalar(select(LogDestinationRow).where(LogDestinationRow.name == name))
    if row is None:
        raise DomainError(ErrorCode.LOG_DESTINATION_NOT_FOUND)
    return row


async def _write_log_destination(
    session: AsyncSession, box: SecretBox | None, change: Change
) -> None:
    """Create, change or delete a log destination, sealing its credential under its id."""
    name = change.key[0]
    if change.action is ChangeAction.DELETE:
        row = await _log_row(session, name)
        await session.execute(
            delete(LogDestinationStatusRow).where(LogDestinationStatusRow.destination_id == row.id)
        )
        await session.delete(row)
        return
    names = _written(change)
    now = _now()
    if change.action is ChangeAction.CREATE:
        row = LogDestinationRow(name=name, created_at=now, updated_at=now)
        row.kind = row.address = row.min_level = row.categories = ""
        session.add(row)
    else:
        row = await _log_row(session, name)
    for field in _LOG_FIELDS:
        if field in names:
            setattr(row, field, change.values[field])
    row.updated_at = now
    if _RESETS_TEST & set(names):
        row.tested_at = None
    await session.flush()
    if "secret" in names:
        value = change.values.get("secret")
        if value is None:
            row.secret_nonce = row.secret_ciphertext = None
        elif isinstance(value, Secret):
            row.secret_nonce, row.secret_ciphertext = _need_box(box).seal(
                log_secret_name(row.id), value.reveal().encode()
            )
        else:
            raise _invalid("secret")
    await session.flush()


async def _role_row(session: AsyncSession, claim_value: str) -> RoleRow:
    """Return the custom role with ``claim_value``.

    Raises:
        DomainError: ``role_not_found``.
    """
    row = await session.scalar(select(RoleRow).where(RoleRow.claim_value == claim_value))
    if row is None:
        raise DomainError(ErrorCode.ROLE_NOT_FOUND)
    return row


async def _write_role(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create, change (the version goes up) or delete a custom role."""
    claim = change.key[0]
    if change.action is ChangeAction.DELETE:
        await session.delete(await _role_row(session, claim))
        return
    names = _written(change)
    if change.action is ChangeAction.CREATE:
        row = RoleRow(
            claim_value=claim, template="", name_nl="", name_en="", permissions="", version=1
        )
        session.add(row)
    else:
        row = await _role_row(session, claim)
        row.version += 1
    for field in ("template", "name_nl", "name_en"):
        if field in names:
            setattr(row, field, str(change.values[field]))
    if "permissions" in names:
        listed = change.values["permissions"]
        if not isinstance(listed, list):
            raise _invalid("permissions")
        row.permissions = " ".join(sorted(str(p) for p in listed))
    await session.flush()


async def _calendar_row(session: AsyncSession, slug: str) -> HolidayCalendarRow:
    """Return the holiday calendar with ``slug``.

    Raises:
        DomainError: ``holiday_calendar_not_found``.
    """
    row = await session.scalar(select(HolidayCalendarRow).where(HolidayCalendarRow.slug == slug))
    if row is None:
        raise DomainError(ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND)
    return row


async def _write_calendar(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create, rename or delete a holiday calendar with its days."""
    slug = change.key[0]
    if change.action is ChangeAction.DELETE:
        row = await _calendar_row(session, slug)
        await session.execute(delete(HolidayRow).where(HolidayRow.calendar_id == row.id))
        await session.delete(row)
        return
    if change.action is ChangeAction.CREATE:
        session.add(HolidayCalendarRow(slug=slug, name=str(change.values["name"])))
    else:
        (await _calendar_row(session, slug)).name = str(change.values["name"])
    await session.flush()


async def _write_holiday(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Add, rename or remove one closed date of a calendar (the calendar is the address head)."""
    calendar = await _calendar_row(session, change.address[0])
    day = date.fromisoformat(change.key[0])
    found = await session.scalar(
        select(HolidayRow).where(HolidayRow.calendar_id == calendar.id, HolidayRow.day == day)
    )
    if change.action is ChangeAction.DELETE:
        if found is not None:
            await session.delete(found)
        return
    if found is None:
        session.add(HolidayRow(calendar_id=calendar.id, day=day, name=str(change.values["name"])))
    else:
        found.name = str(change.values["name"])
    await session.flush()


WRITERS: dict[str, Writer] = {
    PLATFORM_SETTING_KIND: _write_setting,
    PLATFORM_SECRET_KIND: _write_secret,
    API_SETTINGS_KIND: _write_api,
    LOG_DESTINATION_KIND: _write_log_destination,
    ROLE_KIND: _write_role,
    HOLIDAY_CALENDAR_KIND: _write_calendar,
    HOLIDAY_KIND: _write_holiday,
}
"""The writer of every kind this module owns, by kind name."""
