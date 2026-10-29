"""Demo configuration: two organisations with one group each, with weekday opening hours.

``demo`` is the organisation of the development test admins; ``andere`` exists so that every
role can be tested against an organisation and group that are *not* theirs (ADR 0014).
An empty holiday calendar ``nationaal`` is ready for the superuser to fill (ADR 0025); no
organisation has chosen it, so it changes no opening hours.

``seed_closure_examples`` adds what the closure pages need (ADR 0037): a team that is closed
now (with an image, CL-3), a team with a planned closure (without one) and a booking on hold.
Development and test only; the e2e pages of CL-2 are these.

Used by ``python -m app.cli seed-demo`` and by tests. Idempotent: existing groups are left
untouched.
"""

import struct
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.db.models import (
    GroupRow,
    HolidayCalendarRow,
    OrganisationRow,
    ReminderRuleRow,
    ScheduleRuleRow,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.booking import Booking, BookingStatus, Contact, Slot
from app.domain.closure import Closure
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.images import ImageFile, ImageText
from app.domain.ports import Transaction
from app.domain.scheduling import Group, PhoneMode
from app.domain.tokens import derive_token, hash_token, new_token_salt

DEMO_SLUG = "demo"
OTHER_ORGANISATION_SLUG = "andere"
OTHER_GROUP_SLUG = "andere-team"
DEMO_WINDOWS = ((time(9), time(12)), (time(13), time(17)))
DEMO_CAPACITY = 2
DEMO_CALENDAR_SLUG = "nationaal"
CLOSED_GROUP_SLUG = "demo-gesloten"
PLANNED_GROUP_SLUG = "demo-gepland"
EXAMPLE_NOTICES = {
    "nl": "Door een storing in ons systeem kunnen wij u nu niet te woord staan.\n"
    "Wij werken aan een oplossing.",
    "en": "Because of a failure in our system we cannot speak to you right now.\n"
    "We are working on a solution.",
}
EXAMPLE_HEADINGS = {"nl": "Storing bij het loket", "en": "Outage at the desk"}
EXAMPLE_IMAGE_TEXT = ImageText({"nl": "Pictogram van een storing", "en": "Outage pictogram"})
EXAMPLE_CONTACTS = ContactBlock(
    (
        ContactOption.create(
            ContactKind.PHONE,
            "14 020",
            labels={"nl": "Bel ons", "en": "Call us"},
            hours={
                "nl": "Maandag tot en met vrijdag, 9:00 tot 17:00",
                "en": "Weekdays, 9:00 to 17:00",
            },
        ),
        ContactOption.create(ContactKind.WEBSITE, "https://www.example.org/contact"),
        ContactOption.create(ContactKind.ADDRESS, "Stadhuis\nAmstel 1\n1011 PN Amsterdam"),
    )
)
# A made-up citizen: the booking on hold exists only in development and test databases.
EXAMPLE_CITIZEN = Contact.create(
    name="Voorbeeld Inwoner",
    email="inwoner@example.org",
    phone=None,
    phone_mode=PhoneMode.OPTIONAL,
)


@dataclass(frozen=True)
class ClosureExamples:
    """Paths of the example closure pages, for the e2e page list.

    Attributes:
        closed_group: The group page of a team that is closed now.
        planned_closure: The closure page of a team whose closure is still to come.
        suspended_booking: The management page of a booking on hold (a new link each run).
    """

    closed_group: str
    planned_closure: str
    suspended_booking: str


async def seed_demo(engine: AsyncEngine) -> bool:
    """Create the demo organisations and groups; return ``False`` if they already exist."""
    created = await _seed_group(engine, DEMO_SLUG, "Demo-gemeente", DEMO_SLUG, "Demo: videogesprek")
    await _seed_group(
        engine, OTHER_ORGANISATION_SLUG, "Andere gemeente", OTHER_GROUP_SLUG, "Andere: team"
    )
    await _seed_calendar(engine)
    return created


async def _seed_calendar(engine: AsyncEngine) -> None:
    """Create the empty demo holiday calendar unless it exists."""
    async with AsyncSession(engine) as session, session.begin():
        existing = await session.scalar(
            select(HolidayCalendarRow.id).where(HolidayCalendarRow.slug == DEMO_CALENDAR_SLUG)
        )
        if existing is None:
            session.add(HolidayCalendarRow(slug=DEMO_CALENDAR_SLUG, name="Nationale feestdagen"))


async def seed_closure_examples(
    engine: AsyncEngine, now: datetime, token_key: bytes
) -> ClosureExamples:
    """Add a closed team, a planned closure and a booking on hold to the demo organisation.

    Needs ``seed_demo`` first. The teams and closures are created once; every run adds a new
    booking on hold, because only the run that creates a management link can know it.

    Args:
        engine: The database.
        now: The current time; the closed team closed a day earlier.
        token_key: The key that derives management links (ADR 0011).
    """
    created = await _seed_group(
        engine, DEMO_SLUG, "Demo-gemeente", CLOSED_GROUP_SLUG, "Demo: tijdelijk gesloten"
    )
    planned = await _seed_group(
        engine, DEMO_SLUG, "Demo-gemeente", PLANNED_GROUP_SLUG, "Demo: sluiting gepland"
    )
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        closed_group = await _group(tx, CLOSED_GROUP_SLUG)
        if created:
            start = _local(closed_group, now, -1, time(8))
            stored = await tx.add_closure(_example_closure(closed_group, start, None, now))
            image = ImageFile.from_upload(_example_png())
            await tx.save_closure_image(stored.id or 0, EXAMPLE_IMAGE_TEXT, image)
        if planned:
            group = await _group(tx, PLANNED_GROUP_SLUG)
            start = _local(group, now, 14, time(8))
            await tx.add_closure(_example_closure(group, start, start + timedelta(days=2), now))
        (closure,) = await tx.get_closures(closed_group.id, now)
        token = await _suspended_booking(tx, closed_group, closure, now, token_key)
    return ClosureExamples(
        closed_group=f"/g/{CLOSED_GROUP_SLUG}",
        planned_closure=f"/g/{PLANNED_GROUP_SLUG}/closure",
        suspended_booking=f"/b/{token}",
    )


def _example_png(size: int = 96) -> bytes:
    """Return a square PNG in the NL Design System link blue, for the example closure page."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    rows = (b"\x00" + b"\x01\x68\x9b" * size) * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _local(group: Group, now: datetime, days: int, at: time) -> datetime:
    """Return ``at`` on the day ``days`` after today in the group's time zone, in UTC."""
    day = (now.astimezone(group.tz) + timedelta(days=days)).date()
    return datetime.combine(day, at, tzinfo=group.tz).astimezone(UTC)


async def _group(tx: Transaction, slug: str) -> Group:
    """Return the seeded group ``slug``."""
    group = await tx.get_group(slug)
    if group is None:
        raise LookupError(slug)
    return group


def _example_closure(
    group: Group, starts_at: datetime, ends_at: datetime | None, now: datetime
) -> Closure:
    """Return the example closure of ``group`` with a heading and contact options."""
    return Closure(
        None, group.id, starts_at, ends_at, EXAMPLE_NOTICES, now, EXAMPLE_HEADINGS, EXAMPLE_CONTACTS
    )


async def _suspended_booking(
    tx: Transaction, group: Group, closure: Closure, now: datetime, token_key: bytes
) -> str:
    """Store a booking on hold by ``closure`` next week and return its management token."""
    start = _local(group, now, 7, time(10))
    end = start + timedelta(minutes=group.slot_minutes)
    slot = await tx.get_slot(group.id, start) or await tx.add_slot(
        Slot(None, group.id, start, end, DEMO_CAPACITY)
    )
    if slot.id is None or closure.id is None:
        raise ValueError("the slot and the closure must be stored")
    salt = new_token_salt()
    token = derive_token(token_key, salt)
    booking = await tx.add_booking(
        Booking(
            id=None,
            group_id=group.id,
            slot_id=slot.id,
            start=start,
            end=end,
            status=BookingStatus.CONFIRMED,
            contact=EXAMPLE_CITIZEN,
            locale="nl",
            token_hash=hash_token(token),
            created_at=now,
            token_salt=salt,
        )
    )
    await tx.save_booking(booking.suspend(closure.id))
    return token


async def _seed_group(
    engine: AsyncEngine, organisation_slug: str, organisation_name: str, slug: str, name: str
) -> bool:
    """Create a group open on weekdays, and its organisation if needed; skip if the group exists."""
    async with AsyncSession(engine) as session, session.begin():
        existing = await session.scalar(select(GroupRow.id).where(GroupRow.slug == slug))
        if existing is not None:
            return False
        organisation_id = await session.scalar(
            select(OrganisationRow.id).where(OrganisationRow.slug == organisation_slug)
        )
        if organisation_id is None:
            organisation = OrganisationRow(name=organisation_name, slug=organisation_slug)
            session.add(organisation)
            await session.flush()
            organisation_id = organisation.id
        group = GroupRow(
            organisation_id=organisation_id,
            name=name,
            slug=slug,
            timezone="Europe/Amsterdam",
            slot_minutes=30,
            min_notice_minutes=60,
            horizon_days=60,
            phone_mode="optional",
            dispatch_lead_minutes=5,
        )
        session.add(group)
        await session.flush()
        for weekday in range(5):
            for opens, closes in DEMO_WINDOWS:
                session.add(
                    ScheduleRuleRow(
                        group_id=group.id,
                        weekday=weekday,
                        opens=opens,
                        closes=closes,
                        capacity=DEMO_CAPACITY,
                    )
                )
        session.add(
            ReminderRuleRow(
                group_id=group.id, channel="email", minutes_before=24 * 60, enabled=True
            )
        )
    return True
