"""A configured installation to read and write through the configuration document (ADR 0099).

One organisation with a holiday calendar, one fully configured group, a second plain group and
an API client, written as rows. The tests of the readers and writers of groups, API clients and
usage share it.
"""

import json
from datetime import UTC, date, datetime, time
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.config_clients import WRITERS as CLIENT_WRITERS
from app.adapters.db.config_clients import read_api_clients
from app.adapters.db.config_group import WRITERS as GROUP_WRITERS
from app.adapters.db.config_group import read_groups
from app.adapters.db.models import (
    ApiClientGroupRow,
    ApiClientRow,
    GroupContactsRow,
    GroupFormFieldRow,
    GroupHolidayCalendarRow,
    GroupHolidayOpenDayRow,
    GroupRow,
    GroupTextRow,
    HolidayCalendarRow,
    HolidayRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleOverrideRow,
    ScheduleRuleRow,
)
from app.domain.configuration_changes import Change, ChangeAction, RuleContext, Usage
from app.domain.configuration_document import parse_document
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS, V1
from app.domain.configuration_values import Value
from tests.support.config_changes import planned
from tests.support.configuration import allow
from tests.support.configuration_parts import opened

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
CHRISTMAS = date(2026, 12, 25)
CLIENT_ID = "opc_" + "a" * 20
SECRET_HASH = sha256(b"secret").hexdigest()
CONTACTS = {
    "headings": {"nl": "Bel ons"},
    "options": [{"kind": "phone", "value": "030 123 4567", "labels": {}, "hours": {}}],
}


async def seed(engine: AsyncEngine) -> None:
    """Write the installation: organisation ``acme``, groups ``helpdesk`` and ``loket``."""
    async with AsyncSession(engine) as session, session.begin():
        organisation = OrganisationRow(name="Acme", slug="acme")
        session.add(organisation)
        calendar = HolidayCalendarRow(slug="nl", name="Nederland")
        session.add(calendar)
        await session.flush()
        session.add(HolidayRow(calendar_id=calendar.id, day=CHRISTMAS, name="Kerst"))
        helpdesk = GroupRow(
            organisation_id=organisation.id,
            name="Helpdesk",
            slug="helpdesk",
            timezone="Europe/Amsterdam",
            slot_minutes=30,
            min_notice_minutes=60,
            horizon_days=60,
            phone_mode="optional",
            video_host_email="host@voorbeeld.nl",
            holiday_source="own",
            idp_key="helpdesk-idp",
            routing=json.dumps(
                {"subjects": [{"code": "billing", "labels": {"nl": "Facturen", "en": "Billing"}}]}
            ),
        )
        loket = GroupRow(
            organisation_id=organisation.id,
            name="Loket",
            slug="loket",
            timezone="Europe/Amsterdam",
            slot_minutes=15,
            min_notice_minutes=60,
            horizon_days=30,
            phone_mode="hidden",
        )
        session.add_all([helpdesk, loket])
        await session.flush()
        gid = helpdesk.id
        session.add_all(
            [
                ScheduleRuleRow(
                    group_id=gid, weekday=0, opens=time(9), closes=time(17), capacity=3, label="Dag"
                ),
                ScheduleRuleRow(
                    group_id=gid,
                    weekday=1,
                    opens=time(9),
                    closes=time(12),
                    capacity=2,
                    subjects="billing",
                    valid_from=date(2026, 1, 1),
                ),
                ScheduleOverrideRow(group_id=gid, day=date(2026, 12, 24)),
                ScheduleOverrideRow(
                    group_id=gid,
                    day=date(2026, 12, 31),
                    opens=time(9),
                    closes=time(12),
                    capacity=1,
                ),
                ReminderRuleRow(group_id=gid, channel="email", minutes_before=1440, enabled=True),
                GroupHolidayCalendarRow(group_id=gid, calendar_id=calendar.id),
                GroupHolidayOpenDayRow(group_id=gid, day=CHRISTMAS),
                GroupContactsRow(group_id=gid, body=json.dumps(CONTACTS)),
                GroupTextRow(group_id=gid, locale="nl", key="message.signoff", text="Tot ziens"),
                GroupFormFieldRow(
                    group_id=gid,
                    key="klantnummer",
                    position=0,
                    type="short_text",
                    required=True,
                    classification="pii",
                    options="[]",
                    labels=json.dumps({"nl": "Klantnummer", "en": "Customer number"}),
                    hints="{}",
                    option_labels="{}",
                    retired=False,
                ),
                GroupFormFieldRow(
                    group_id=gid,
                    key="oud",
                    position=1,
                    type="short_text",
                    required=False,
                    classification="pii",
                    options="[]",
                    labels=json.dumps({"nl": "Oud", "en": "Old"}),
                    hints="{}",
                    option_labels="{}",
                    retired=True,
                ),
            ]
        )
        client = ApiClientRow(
            client_id=CLIENT_ID,
            name="Bot",
            organisation_id=organisation.id,
            scopes="availability:read",
            networks="10.0.0.0/8",
            enabled=True,
            secret_hash=SECRET_HASH,
            secret_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            created_at=NOW,
        )
        session.add(client)
        await session.flush()
        session.add(ApiClientGroupRow(client_pk=client.id, group_id=gid))


async def installation(session: AsyncSession) -> Entity:
    """Return the installation as the readers see it, around a stub platform and organisations."""
    groups = await read_groups(session)
    clients = await read_api_clients(session, None)
    rows = await session.execute(select(OrganisationRow.slug, OrganisationRow.name))
    organisations = tuple(
        Entity(
            "organisation",
            (slug,),
            fields={"name": name},
            children={"groups": groups.get(slug, ())},
        )
        for slug, name in rows.all()
    )
    day = Entity("holiday", (CHRISTMAS.isoformat(),), fields={"name": "Kerst"})
    calendar = Entity(
        "holiday_calendar", ("nl",), fields={"name": "Nederland"}, children={"days": (day,)}
    )
    platform = Entity("platform", children={"holiday_calendars": (calendar,)})
    return Entity(
        "installation",
        children={"platform": (platform,), "organisations": organisations, "api_clients": clients},
    )


async def apply_document(
    engine: AsyncEngine, data: dict[str, Any], usage: Usage | None = None
) -> Plan:
    """Plan a document against the stored installation and, when it is clean, write it."""
    async with AsyncSession(engine) as session, session.begin():
        document = opened(parse_document({"format": V1.name, **data}, FORMATS))
        plan = plan_document(
            document,
            await installation(session),
            usage=usage or Usage(),
            may_change=allow,
            context=RuleContext(now=NOW),
            settings_version=1,
        )
        if plan.ok:
            for planned in plan.changes:
                writer = {**GROUP_WRITERS, **CLIENT_WRITERS}[planned.kind]
                await writer(session, None, planned)
        return plan


async def reread(engine: AsyncEngine) -> dict[str, tuple[Entity, ...]]:
    """Return the groups as they are stored now."""
    async with AsyncSession(engine) as session:
        return await read_groups(session)


def change(
    kind: str,
    address: tuple[str, ...],
    action: ChangeAction,
    values: dict[str, Value] | None = None,
    fields: tuple[str, ...] | None = None,
) -> Change:
    """Return a planned change as the planner hands it to a writer."""
    key = address[-2:] if kind == "group_text" else address[-1:]
    return planned(kind, address, action, values, fields, key=key)
