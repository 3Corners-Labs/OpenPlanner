"""Holiday calendars across the version-1 document (ADR 0099 point 7, ADR 0022).

Calendars sit on the platform and are chosen by organisations and groups, so their rules reach
across slices: a chosen calendar must exist, a calendar in use cannot be deleted, and a group is
closed on the holidays of the calendars it observes (its own, or its organisation's) except on
the days it keeps open. ``CalendarWorld`` reads all of that from a tree once; rules ask it per
group instead of walking the tree again.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date

from app.domain.configuration_changes import IntegrityRule, Refusal, Resolution
from app.domain.configuration_checks import strings_of
from app.domain.configuration_tree import Address, Entity, walk
from app.domain.configuration_v1_names import HOLIDAY_CALENDAR_KIND, HOLIDAY_KIND
from app.domain.configuration_values import Value
from app.domain.errors import ErrorCode

CALENDARS = "holiday_calendars"
OPEN_DAYS = "holiday_open_days"
SOURCE = "holiday_source"
OWN = "own"


def parse_days(texts: list[str]) -> list[date]:
    """Return the ISO dates among ``texts`` (anything else is left out)."""
    found: list[date] = []
    for text in texts:
        try:
            found.append(date.fromisoformat(text))
        except ValueError:
            continue
    return found


@dataclass(frozen=True)
class CalendarWorld:
    """The holiday calendars of one tree and who observes them.

    Attributes:
        days: The holidays of every calendar, by calendar slug.
        users: The fields of every object that names calendars or open days (an organisation
            or a group), by address.
    """

    days: Mapping[str, frozenset[date]]
    users: Mapping[Address, Mapping[str, Value]]

    def observed(self, address: Address) -> frozenset[date]:
        """Return the holidays of the calendars the object at ``address`` observes.

        A group observes its own calendars when its source is ``own``, else its
        organisation's (the first part of its address).
        """
        fields = self.users.get(address, {})
        source = fields if fields.get(SOURCE) == OWN else self.users.get(address[:1], {})
        found: set[date] = set()
        for slug in strings_of(source, CALENDARS):
            found |= self.days.get(slug, frozenset())
        return frozenset(found)

    def closed(self, address: Address) -> frozenset[date]:
        """Return the holidays on which the group at ``address`` is closed (open days left out)."""
        fields = self.users.get(address, {})
        return self.observed(address) - set(parse_days(strings_of(fields, OPEN_DAYS)))


def world_of(root: Entity) -> CalendarWorld:
    """Read the calendars and their observers from a tree, in one walk."""
    days: dict[str, frozenset[date]] = {}
    users: dict[Address, Mapping[str, Value]] = {}
    for address, entity in walk(root):
        if entity.kind == HOLIDAY_CALENDAR_KIND:
            holidays = (child for child in entity.children.get("days", ()))
            days[address[-1]] = frozenset(parse_days([child.key[0] for child in holidays]))
        elif CALENDARS in entity.fields or OPEN_DAYS in entity.fields:
            users[address] = entity.fields
    return CalendarWorld(days, users)


def _calendar_not_found(resolution: Resolution) -> Iterator[Refusal]:
    """Refuse a chosen holiday calendar that the result does not have."""
    known = world_of(resolution.resulting).days
    for change in resolution.changes:
        if CALENDARS not in change.fields:
            continue
        if any(slug not in known for slug in strings_of(change.values, CALENDARS)):
            yield Refusal(change.path, ErrorCode.HOLIDAY_CALENDAR_NOT_FOUND, CALENDARS)


def _calendar_in_use(resolution: Resolution) -> Iterator[Refusal]:
    """Refuse deleting a holiday calendar an organisation or group still names."""
    deleted = {c.key[0]: c.path for c in resolution.deletions if c.kind == HOLIDAY_CALENDAR_KIND}
    if not deleted:
        return
    for fields in world_of(resolution.resulting).users.values():
        for slug in strings_of(fields, CALENDARS):
            if slug in deleted:
                yield Refusal(deleted.pop(slug), ErrorCode.HOLIDAY_CALENDAR_IN_USE)


def changes_holidays(resolution: Resolution) -> bool:
    """Whether the plan changes a calendar, its days, or which calendars an owner chooses.

    Any of them can close days, and so lower the staffing of groups it does not touch.
    """
    return any(
        change.kind in (HOLIDAY_CALENDAR_KIND, HOLIDAY_KIND) or CALENDARS in change.fields
        for change in (*resolution.changes, *resolution.deletions)
    )


INTEGRITY: tuple[IntegrityRule, ...] = (_calendar_not_found, _calendar_in_use)
"""The rules across objects that concern holiday calendars; the platform slice carries them."""
