# Staffing of a capacity pool

A counted capacity pool says how many people work for the services that draw on it, and when.
That is what you set on these pages: the pool's **time zone**, its **weekly blocks** and its
**exceptions**. You open them from **Organisations → organisation → Overview → Skills and
capacity**, with **Staffing** next to the pool. Only organisation admins and the superuser may
change a pool's staffing (the right to manage capacity), each for their own organisations, and the
portal may ask you to log in again first. A pool of another organisation, an archived pool and an
existing pool (the staffing of a service itself) have no staffing pages.

A pool counts people, not names. Do not count the same person in a pool and in a service's own
staffing, or in two pools, at the same moment: the portal plans for two people where there is one,
and it cannot check that for you yet.

No booking reads a pool's staffing yet. You can already enter it, change it and see the
shortfalls; flows start to use it in a later version.

## Time zone

The blocks of a pool are read in the pool's own time zone, also when the services that draw on
the pool work in another one. Summer and winter time follow from the zone. Choose the zone first:
the weekly blocks and exceptions of a pool can be entered only after that.

## Shortfalls ahead

When more places are held in the pool than people are counted, the page lists those moments for
the coming days, with how many people are counted and how many places are held. How far ahead is
the organisation setting **Shortfall look-ahead (days)** (default 14). Nothing is moved or
cancelled: existing appointments stay, whatever you change in the staffing. Adjust the staffing,
or move appointments yourself.

## Weekly blocks

A block runs from a start to an end time on a weekday, with the number of people counted at the
same time. Blocks of one day may not overlap; touching blocks are fine. Enter 0 people for a
lunch break. **Change** edits a block, **Split** cuts it in two touching blocks (the second part
gets its own number and label, for example 0 and "Lunch"), **Remove** deletes it. A change
applies to appointments made from then on.

Each block says what it is kept for: **appointments**, **direct call** (work without an
appointment), or both. Appointments are never placed in a block kept for direct calls, and the
other way round. A block can have a label that only administrators see, and a period in which it
applies.

## Exceptions

An exception replaces the weekly blocks of one date: no staffing at all, or other blocks. The date
may not lie in the past. The holidays of the organisation's calendars close the pool as well,
unless an exception gives that date its own blocks.

Every change goes to the [audit log](audit.md) with the pool's code, never with a name or a number.
