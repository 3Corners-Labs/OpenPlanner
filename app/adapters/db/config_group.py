"""Groups in a configuration document: read from and written to the database (ADR 0099 step 2).

``read_groups`` turns every group row, with its staffing, reminders, form, holiday choice,
contacts, routing and texts, into the ``Entity`` the version-1 format describes
(``app.domain.configuration_v1_group``), in the same normalised form a document carries, so a
document that restates what is stored plans to nothing. ``WRITERS`` applies one planned
``Change`` inside the caller's transaction: it writes columns and replaces the whole of a list
the change names, and writes neither audit events nor the settings version (the caller does
both once per apply). Raw SQL is not used; the rows are the ORM's. No value is logged.
"""

import json
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.contact_json import contacts_block, contacts_json
from app.adapters.db.form_fields import FormFieldQueries
from app.adapters.db.models import (
    GroupContactsRow,
    GroupHolidayCalendarRow,
    GroupHolidayOpenDayRow,
    GroupRow,
    GroupTextRow,
    HolidayCalendarRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleOverrideRow,
    ScheduleRuleRow,
    SlotRow,
)
from app.adapters.db.routing_json import (
    routing_from_json as stored_routing,
)
from app.adapters.db.routing_json import (
    subjects_column,
    subjects_from_column,
)
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_group_parts import (
    _definition,
    _form_json,
    items_of,
    override_json,
    rule_json,
    schedule_of,
)
from app.domain.configuration_v1_names import GROUP_KIND, GROUP_TEXT_KIND
from app.domain.configuration_values import Json, Value
from app.domain.contacts import block_from_json, block_to_json
from app.domain.errors import DomainError, ErrorCode
from app.domain.form_fields import FieldDefinition, FormDefinition
from app.domain.routing_codec import routing_from_json as strict_routing
from app.domain.routing_codec import routing_to_json
from app.domain.scheduling import Offer, ScheduleOverride, ScheduleRule, TimeWindow

type Writer = Callable[[AsyncSession, SecretBox | None, Change], Awaitable[None]]
"""Applies one planned change inside the caller's transaction (the contract of step 2)."""

COLUMNS = (
    "name",
    "timezone",
    "slot_minutes",
    "min_notice_minutes",
    "horizon_days",
    "phone_mode",
    "video_host_email",
    "wxcc_business_address",
    "wxcc_channel",
    "wxcc_routing_value",
    "not_picked_up_minutes",
    "direct_call_max_concurrent",
    "dispatch_lead_minutes",
    "dispatch_kind",
    "dispatch_fallback",
    "fallback_space_room_id",
    "participant_change_lead_minutes",
    "planner_change_min_lead_minutes",
    "trial_call_enabled",
    "trial_call_minutes",
    "trial_calls_per_booking",
    "join_client",
    "embedding_enabled",
    "queue_visible_from_minutes",
    "queue_free_pick_allowed",
    "max_bookings_per_email",
    "calendar_invite_address",
    "holiday_source",
)
"""The group fields stored one to one in a column of ``groups``."""


class _Forms(FormFieldQueries):
    """The form queries on a bare session, so the form is written as ``FormService`` does."""

    def __init__(self, session: AsyncSession) -> None:
        """Keep the session the queries run on."""
        self._session = session


def _list(items: Iterable[Json]) -> list[Json]:
    """Return ``items`` as the list a document field holds."""
    return list(items)


# --- reading ------------------------------------------------------------------------------


def _rule_of(row: ScheduleRuleRow) -> ScheduleRule:
    """Map a weekly rule row to the domain."""
    window = TimeWindow(
        row.opens,
        row.closes,
        row.capacity,
        row.label,
        Offer(row.offer),
        subjects_from_column(row.subjects),
    )
    return ScheduleRule(row.weekday, window, row.valid_from, row.valid_to)


def _override_of(row: ScheduleOverrideRow) -> ScheduleOverride:
    """Map an exception row; empty window columns are a closed day."""
    if row.opens is None or row.closes is None or row.capacity is None:
        return ScheduleOverride(row.day, None)
    window = TimeWindow(
        row.opens,
        row.closes,
        row.capacity,
        row.label,
        Offer(row.offer),
        subjects_from_column(row.subjects),
    )
    return ScheduleOverride(row.day, window)


def _grouped[Row](rows: Iterable[tuple[int, Row]]) -> dict[int, list[Row]]:
    """Collect ``(group id, row)`` pairs by group id, keeping the order."""
    grouped: dict[int, list[Row]] = defaultdict(list)
    for group_id, row in rows:
        grouped[group_id].append(row)
    return grouped


async def read_groups(session: AsyncSession) -> dict[str, tuple[Entity, ...]]:
    """Return the groups of every organisation as document objects, by organisation slug.

    Each group is a ``group`` object with its fields in the form a document carries and the
    ``texts`` it owns as children. The closure scenarios of a group are read elsewhere.
    """
    pairs = (
        await session.execute(
            select(GroupRow, OrganisationRow.slug)
            .join(OrganisationRow, OrganisationRow.id == GroupRow.organisation_id)
            .order_by(GroupRow.id)
        )
    ).all()
    rules = _grouped(
        (r.group_id, r)
        for r in await session.scalars(
            select(ScheduleRuleRow).order_by(
                ScheduleRuleRow.weekday, ScheduleRuleRow.opens, ScheduleRuleRow.id
            )
        )
    )
    overrides = _grouped(
        (o.group_id, o)
        for o in await session.scalars(
            select(ScheduleOverrideRow).order_by(
                ScheduleOverrideRow.day, ScheduleOverrideRow.opens, ScheduleOverrideRow.id
            )
        )
    )
    reminders = _grouped(
        (r.group_id, r)
        for r in await session.scalars(select(ReminderRuleRow).order_by(ReminderRuleRow.id))
    )
    calendars = _grouped(
        (group_id, slug)
        for group_id, slug in (
            await session.execute(
                select(GroupHolidayCalendarRow.group_id, HolidayCalendarRow.slug)
                .join(
                    HolidayCalendarRow, HolidayCalendarRow.id == GroupHolidayCalendarRow.calendar_id
                )
                .order_by(HolidayCalendarRow.slug)
            )
        ).all()
    )
    open_days = _grouped(
        (group_id, day.isoformat())
        for group_id, day in (
            await session.execute(
                select(GroupHolidayOpenDayRow.group_id, GroupHolidayOpenDayRow.day).order_by(
                    GroupHolidayOpenDayRow.day
                )
            )
        ).all()
    )
    contacts = {c.group_id: c.body for c in await session.scalars(select(GroupContactsRow))}
    texts = _grouped(
        (t.group_id, t)
        for t in await session.scalars(
            select(GroupTextRow).order_by(GroupTextRow.locale, GroupTextRow.key)
        )
    )
    forms = _Forms(session)
    result: dict[str, list[Entity]] = defaultdict(list)
    for row, organisation in pairs:
        form = await forms.get_form(row.id)
        fields: dict[str, Value] = {name: getattr(row, name) for name in COLUMNS}
        fields["idp_key"] = None if (row.idp_key or row.slug) == row.slug else row.idp_key
        fields["archived"] = row.archived_at is not None
        fields["holiday_calendars"] = _list(calendars.get(row.id, []))
        fields["holiday_open_days"] = _list(open_days.get(row.id, []))
        fields["form_fields"] = _list(_form_json(f) for f in (*form.fields, *form.retired))
        fields["schedule_rules"] = _list(rule_json(_rule_of(r)) for r in rules.get(row.id, []))
        fields["schedule_overrides"] = _list(
            override_json(_override_of(o)) for o in overrides.get(row.id, [])
        )
        fields["reminder_rules"] = _list(
            {"channel": r.channel, "minutes_before": r.minutes_before, "enabled": r.enabled}
            for r in reminders.get(row.id, [])
        )
        fields["routing"] = cast("Json", routing_to_json(stored_routing(row.routing)))
        block = contacts_block(contacts[row.id]) if row.id in contacts else None
        fields["contacts"] = (
            None if block is None or block.is_empty else cast("Json", block_to_json(block))
        )
        own_texts = tuple(
            Entity(GROUP_TEXT_KIND, (t.locale, t.key), fields={"text": t.text})
            for t in texts.get(row.id, [])
        )
        result[organisation].append(
            Entity(GROUP_KIND, (row.slug,), fields=fields, children={"texts": own_texts})
        )
    return {slug: tuple(groups) for slug, groups in result.items()}


# --- writing ------------------------------------------------------------------------------


def _invalid(field: str) -> DomainError:
    """Return the refusal for a value the database cannot take, naming the field."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, field)


async def _organisation_id(session: AsyncSession, slug: str) -> int:
    """Return the id of an organisation by slug."""
    found = await session.scalar(select(OrganisationRow.id).where(OrganisationRow.slug == slug))
    if found is None:
        raise DomainError(ErrorCode.ORGANISATION_NOT_FOUND, "organisation")
    return int(found)


async def _group_row(session: AsyncSession, slug: str) -> GroupRow:
    """Return the group row with ``slug``."""
    row = await session.scalar(select(GroupRow).where(GroupRow.slug == slug))
    if row is None:
        raise DomainError(ErrorCode.GROUP_NOT_FOUND, "group")
    return row


def _items(values: Mapping[str, Value], name: str) -> list[dict[str, Json]]:
    """Return the objects of a list field the change holds."""
    return items_of(values.get(name))


async def _replace_schedule(
    session: AsyncSession, group_id: int, values: Mapping[str, Value], names: set[str]
) -> None:
    """Replace the group's weekly rules and exceptions as a whole, as far as the change says."""
    rules, overrides = schedule_of(values)
    if "schedule_rules" in names:
        await session.execute(delete(ScheduleRuleRow).where(ScheduleRuleRow.group_id == group_id))
        session.add_all(
            ScheduleRuleRow(
                group_id=group_id,
                weekday=rule.weekday,
                opens=rule.window.opens,
                closes=rule.window.closes,
                capacity=rule.window.capacity,
                label=rule.window.label,
                offer=rule.window.offer.value,
                subjects=subjects_column(rule.window.subjects),
                valid_from=rule.valid_from,
                valid_to=rule.valid_to,
            )
            for rule in rules
        )
    if "schedule_overrides" in names:
        await session.execute(
            delete(ScheduleOverrideRow).where(ScheduleOverrideRow.group_id == group_id)
        )
        for override in overrides:
            window = override.window
            session.add(
                ScheduleOverrideRow(
                    group_id=group_id,
                    day=override.day,
                    opens=window.opens if window else None,
                    closes=window.closes if window else None,
                    capacity=window.capacity if window else None,
                    label=window.label if window else None,
                    offer=window.offer.value if window else Offer.SCHEDULED.value,
                    subjects=subjects_column(window.subjects) if window else None,
                )
            )


async def _replace_holidays(
    session: AsyncSession, group_id: int, values: Mapping[str, Value], names: set[str]
) -> None:
    """Replace the group's own calendars and open days as far as the change says."""
    if "holiday_calendars" in names:
        slugs = [str(s) for s in cast("list[Json]", values.get("holiday_calendars") or [])]
        found = await session.execute(
            select(HolidayCalendarRow.slug, HolidayCalendarRow.id).where(
                HolidayCalendarRow.slug.in_(slugs)
            )
        )
        ids = {slug: calendar_id for slug, calendar_id in found.all()}
        if len(ids) != len(set(slugs)):
            raise DomainError(ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND, "holiday_calendars")
        await session.execute(
            delete(GroupHolidayCalendarRow).where(GroupHolidayCalendarRow.group_id == group_id)
        )
        session.add_all(
            GroupHolidayCalendarRow(group_id=group_id, calendar_id=ids[slug])
            for slug in sorted(set(slugs))
        )
    if "holiday_open_days" in names:
        days = {date.fromisoformat(str(d)) for d in cast("list[Json]", values["holiday_open_days"])}
        await session.execute(
            delete(GroupHolidayOpenDayRow).where(GroupHolidayOpenDayRow.group_id == group_id)
        )
        session.add_all(GroupHolidayOpenDayRow(group_id=group_id, day=d) for d in sorted(days))


async def _replace_reminders(
    session: AsyncSession, group_id: int, values: Mapping[str, Value]
) -> None:
    """Replace the group's reminder rules as a whole."""
    await session.execute(delete(ReminderRuleRow).where(ReminderRuleRow.group_id == group_id))
    session.add_all(
        ReminderRuleRow(
            group_id=group_id,
            channel=str(item["channel"]),
            minutes_before=int(cast("int", item["minutes_before"])),
            enabled=bool(item.get("enabled", True)),
        )
        for item in _items(values, "reminder_rules")
    )


async def _write_form(session: AsyncSession, group_id: int, values: Mapping[str, Value]) -> None:
    """Write the form as ``FormService`` does: the list is the form, the rest is retired.

    A field the list leaves out is kept as retired, never deleted, so its key stays reserved
    and its labels explain old answers. The version goes up when the form changed, and the
    stored answers of a field that was retired or became personal data are raised to personal
    data (ADR 0055).
    """
    forms = _Forms(session)
    old = await forms.get_form(group_id)
    before = {f.key: f for f in (*old.fields, *old.retired)}
    active: list[FieldDefinition] = []
    retired: list[FieldDefinition] = []
    for item in _items(values, "form_fields"):
        definition = _definition(item)
        (retired if item.get("retired") else active).append(
            replace(definition, retired=bool(item.get("retired")))
        )
    listed = {f.key for f in (*active, *retired)}
    retired.extend(
        f if f.retired else replace(f, retired=True)
        for key, f in before.items()
        if key not in listed
    )
    if (tuple(active), tuple(retired)) == (old.fields, old.retired):
        return
    await forms.save_form(group_id, FormDefinition(old.version + 1, tuple(active), tuple(retired)))
    for field in (*active, *retired):
        was = before.get(field.key)
        if was is None:
            continue
        newly_retired = field.retired and not was.retired
        became_pii = (
            field.classification != was.classification and field.classification.value == "pii"
        )
        if newly_retired or became_pii:
            await forms.raise_answers_to_pii(group_id, field.key)


async def _write_contacts(session: AsyncSession, group_id: int, value: Value) -> None:
    """Replace or, with ``None``, remove the group's own contact block."""
    await session.execute(delete(GroupContactsRow).where(GroupContactsRow.group_id == group_id))
    if value is not None:
        session.add(GroupContactsRow(group_id=group_id, body=contacts_json(block_from_json(value))))


async def write_group(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a group: create it, change the named fields, or delete it.

    Raises:
        DomainError: ``organisation_not_found``, ``group_not_found`` or
            ``holiday_calendar_not_found`` when a name the change uses does not resolve.
    """
    slug = change.key[0]
    if change.action is ChangeAction.DELETE:
        row = await _group_row(session, slug)
        # Only a group nothing refers to reaches here (the plan refused the others), as for the
        # eraser: its slots first (a booking would fail loudly), then the row and its cascades.
        await session.execute(delete(SlotRow).where(SlotRow.group_id == row.id))
        await session.execute(delete(GroupRow).where(GroupRow.id == row.id))
        return
    values = change.values
    if change.action is ChangeAction.CREATE:
        names = set(values)
        row = GroupRow(
            organisation_id=await _organisation_id(session, change.address[0]),
            slug=slug,
            idp_key=slug,
            **{name: values[name] for name in COLUMNS if name in values},
        )
        session.add(row)
        await session.flush()
    else:
        names = set(change.fields)
        row = await _group_row(session, slug)
        for name in COLUMNS:
            if name in names:
                setattr(row, name, values[name])
    if "idp_key" in names:
        row.idp_key = str(values.get("idp_key") or slug)
    if "archived" in names and values.get("archived") and row.archived_at is None:
        row.archived_at = datetime.now(UTC)
    if "routing" in names:
        routing = values.get("routing")
        row.routing = (
            None
            if routing is None
            else json.dumps(routing_to_json(strict_routing(routing)), sort_keys=True)
        )
    await session.flush()
    await _replace_schedule(session, row.id, values, names)
    await _replace_holidays(session, row.id, values, names)
    if "reminder_rules" in names:
        await _replace_reminders(session, row.id, values)
    if "form_fields" in names:
        await _write_form(session, row.id, values)
    if "contacts" in names:
        await _write_contacts(session, row.id, values.get("contacts"))
    await session.flush()


async def write_group_text(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Apply one change to a group's text: set it, or with a delete remove the override."""
    group = await _group_row(session, change.address[1])
    locale, key = change.key
    if change.action is ChangeAction.DELETE:
        await session.execute(
            delete(GroupTextRow).where(
                GroupTextRow.group_id == group.id,
                GroupTextRow.locale == locale,
                GroupTextRow.key == key,
            )
        )
        return
    await session.merge(
        GroupTextRow(group_id=group.id, locale=locale, key=key, text=str(change.values["text"]))
    )
    await session.flush()


WRITERS: dict[str, Writer] = {GROUP_KIND: write_group, GROUP_TEXT_KIND: write_group_text}
"""The writers of this module, by the name of the kind they apply."""
