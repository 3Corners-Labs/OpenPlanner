"""Shared builders and constants for domain and adapter tests.

All tests use the same calendar: "now" is Thursday 1 January 2026 08:00 UTC; the group is
open on Monday 5 January 09:00-10:00 Amsterdam time (08:00-09:00 UTC) in 30-minute slots.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, time
from typing import Any

from app.domain.authorization import Principal, Role
from app.domain.scheduling import DispatchKind, Group, Offer, TimeWindow
from app.domain.services import BookingReceipt, BookingService, ContactInput

NOW = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
MONDAY = date(2026, 1, 5)
SLOT_0900 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # 09:00 Amsterdam (CET)
SLOT_0930 = datetime(2026, 1, 5, 8, 30, tzinfo=UTC)
TUESDAY_0900 = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)
ADA = ContactInput(
    first_name="Ada", last_name="Lovelace", email="ada@example.org", phone="06 1234 5678"
)
DEMO_SUPERUSER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
DEMO_ORG_ADMIN = Principal(
    "o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset()
)
"""Admins for domain tests: the superuser and an organisation admin of ``demo``."""
TEST_TOKEN_KEY = b"k" * 32
"""Server key for deriving management tokens in tests (never used outside tests)."""


def window(
    opens: str = "09:00", closes: str = "10:00", capacity: int = 2, offer: Offer = Offer.SCHEDULED
) -> TimeWindow:
    """Return a local staffing block; ``offer`` says whether it takes direct calls (ADR 0071)."""
    return TimeWindow(time.fromisoformat(opens), time.fromisoformat(closes), capacity, offer=offer)


def make_group(**overrides: object) -> Group:
    """Return a valid group, with any field overridden."""
    fields: dict[str, object] = {
        "id": 1,
        "organisation_id": 1,
        "name": "Aangifte",
        "slug": "aangifte-2026",
        "timezone": "Europe/Amsterdam",
        "slot_minutes": 30,
    }
    fields.update(overrides)
    return Group(**fields)  # type: ignore[arg-type]


def with_team_invite(group: Group) -> Group:
    """Return ``group`` on the own queue, sending its team a calendar invite (ADR 0043)."""
    return replace(
        group,
        dispatch_kind=DispatchKind.OPENPLANNER_QUEUE,
        calendar_invite_address="team@example.org",
    )


async def book_confirmed(service: BookingService, **arguments: Any) -> BookingReceipt:
    """Book with ``service.book(**arguments)`` and confirm it, as the citizen would by link."""
    receipt = await service.book(**arguments)
    confirmed = await service.confirm(receipt.management_token)
    return BookingReceipt(confirmed, receipt.management_token)


async def book_pending(
    service: BookingService,
    start: datetime = SLOT_0900,
    slug: str = "aangifte",
    contact: ContactInput = ADA,
) -> BookingReceipt:
    """Book ``start`` for ``contact`` (Ada) in group ``slug``; it awaits confirmation."""
    return await service.book(group_slug=slug, start=start, contact=contact, locale="nl")


def citizen(number: int) -> ContactInput:
    """Return Ada's details under address ``number``: another citizen for the address limit."""
    return replace(ADA, email=f"citizen{number}@example.org")


async def book(
    service: BookingService,
    start: datetime = SLOT_0900,
    slug: str = "aangifte",
    contact: ContactInput = ADA,
) -> BookingReceipt:
    """Book ``start`` for ``contact`` (Ada) in ``slug`` and confirm it, as by email link."""
    return await book_confirmed(service, group_slug=slug, start=start, contact=contact, locale="nl")
