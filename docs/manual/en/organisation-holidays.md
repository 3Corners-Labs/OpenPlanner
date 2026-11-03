# Holidays of an organisation

Holiday calendars list days on which groups are closed, such as New Year's Day or King's Day.
The superuser maintains the calendars and their dates once, for the whole installation. On this
screen an organisation chooses which calendars apply to its groups. Open it through
**Organisations → organisation → Holidays**. Organisation admins choose for their own
organisations; the superuser can choose for any organisation. Before you save, the portal may ask
you to log in again.

## Choosing calendars

<!-- field: calendars -->
### Calendars

The page lists every calendar that exists, each with a checkbox. Tick the calendars that apply
to the organisation's groups and press **Save**. Ticking nothing means that no holiday applies.
A calendar that was added to a group's holidays closes that group on each date in the calendar,
unless the group does something else on that date (see below).

If there are no calendars yet, the page says so; the superuser makes them first. The Dutch
national holidays of a year (New Year's Day, Easter, King's Day, Liberation Day, Ascension Day,
Whitsun and Christmas) can be added to a calendar in one action by the superuser. Good Friday is
not in it; the superuser adds it where it applies.

## What it changes for groups

- A group that **follows the organisation** (the default) uses your choice at once. When you
  change the choice, the group's holidays change with it.
- A group that made **its own choice** does not change. Group admins choose this on the group's
  [staffing screens](group-staffing.md) under **Holidays**.
- A group can keep a single holiday open anyway, or give it its own hours with a staffing
  exception. That wins over the calendar. An organisation cannot keep a day open for all its
  groups; to do that, choose another calendar.
- On a closed day participants cannot book and no direct call starts. Bookings that already
  exist keep their slot.

If you saved without changing anything, the portal says so. The choice is in the audit log, with
the organisation. The names of holidays are not shown to participants yet; a closed day simply
has no times.
