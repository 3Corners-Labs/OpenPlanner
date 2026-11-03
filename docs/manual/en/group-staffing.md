# Group staffing

The staffing says when a group's agents are on duty and how many there are. Participants can
only book, or start a direct call, when the staffing allows it. Three tabs of a group belong
together:

- **Staffing**: the weekly blocks.
- **Staffing exceptions**: a single date that differs from the week.
- **Holidays**: which holiday calendars apply to the group.

You open them through **Groups → group → Staffing** (or **Staffing exceptions**, **Holidays**).
Group admins, organisation admins and the superuser can change them, and a custom role with the
right to manage the staffing. Compliance and privacy (read-only) and agents cannot. Before you
save, the portal may ask you to log in again.

A change applies to new bookings. Existing bookings always keep their slot, also when you lower
the number of agents afterwards. Then a slot may have more bookings than agents; see
[Group agenda](group-agenda.md) for how to solve such a shortfall. The three pages say how many
slots in the next 14 days are short. Every change is in the audit log.

## Weekly staffing

The page shows the week from Monday to Sunday. A day holds one or more **blocks**. A block has a
start, an end, a number of agents and, if you like, a label. Blocks that touch each other and
have agents are divided together into slots of the group's call duration (see
[Group settings](group-settings.md)); participants book a slot. A day without blocks has no
staff: you see *No staff* and nobody can book that day.

Use **Add** under a day to add a block there, or the form **Add a block** below the week. **Change**,
**Split** and **Remove** are next to each block.

<!-- field: capacity -->
### Number of agents

The number of agents in the block, from 0 to 1000. That many participants can book the same
slot. **0** is allowed and means: nobody on duty. Use it for lunch, for example: the week then
shows that the break is on purpose and not forgotten. A block with 0 agents has no slots and no
direct calls. A block shows *no agents* in words, not only in a different colour.

<!-- field: label -->
### Label

An optional note on the block, such as *Lunch* or *Team meeting*, at most 50 characters and on
one line. Only administrators see it; participants and logs never get it.

<!-- field: valid_from -->
### Valid from and until

Optional. With these dates the block only applies in that period, for example for a summer
schedule. Leave both empty and the block always applies. Two blocks on the same day of the week
may not overlap in time while their periods overlap. Touching is fine (09:00-12:00 and
12:00-13:00). If blocks overlap, the portal refuses the change and says so at the field.

<!-- field: offer -->
### Offer: appointments or direct call

Per block you choose what participants can do:

- **Appointments** (the default): the block offers slots.
- **Direct call**: participants can start a video call right away, without an appointment. The
  block then offers no slots and also ends a run of slots, like a block without agents.
- **Appointments and direct call**: both.

A direct call can only start while the current time lies in a block with agents that offers
direct calls. The maximum number of simultaneous direct calls and the time after which a call
counts as not picked up are group settings, not set per block (see
[Group settings](group-settings.md)). A team can thus take direct calls in the morning and
appointments in the afternoon. A block with 0 agents never offers direct calls.

<!-- field: split_at -->
### Splitting a block

**Split** turns one block into two touching blocks, for example to put a lunch break in the middle
of a morning. On the form you choose the time of the split, which must lie strictly between the
start and the end of the block. The first part keeps the number of agents, label, offer and
validity period; for the second part you choose the number of agents and a label (for example
0 and *Lunch*). The second part keeps the offer and period of the original block and ends where
the block ended.

### Changing and removing a block

**Change** opens the form with the current values. The new block may overlap its own old times,
but not other blocks. **Remove** deletes the block at once. If a change did not alter anything,
the portal says so.

## Staffing exceptions

An exception replaces the weekly blocks of **one date**. Open it through the tab **Staffing
exceptions**. The list shows the coming exceptions with the date and what applies: *No staff* or
the time with number of agents and, when it is not the default, the offer. **Change** and
**Remove** are next to every exception.

<!-- field: kind -->
### Kind

- **No staff all day**: the group is closed that date, nobody can book and no direct call starts.
- **Own block**: only the block you enter applies that date (from, until, number of agents,
  offer, label as with a weekly block). Several exceptions on one date are possible when they do
  not overlap. A closed day cannot also have blocks.

<!-- field: day -->
### Date

The date of the exception. A date in the past is refused. A change applies to new bookings;
bookings already made on that date keep their slot.

## Holidays

The tab **Holidays** says which holiday calendars apply to the group. The superuser maintains
the calendars; an organisation chooses which ones apply to its groups (see
[Holidays of an organisation](organisation-holidays.md)).

The page lists the holidays of the next year from the calendars that apply to the group, with
what the group does on each:

- **Closed**: the group is closed; participants cannot book that day. **Open anyway** keeps the
  group open as usual that day: the weekly blocks apply.
- **Open as usual**: the group kept this holiday open. **Close again** undoes it.
- **Own staffing exception**: the group has an exception on that date, which always wins. That is
  how a holiday can still have its own hours. **See staffing exceptions** opens the tab.

<!-- field: source -->
### Which calendars apply

- **Follow the organisation** (default): the group uses the calendars the organisation chose.
  The page shows which ones those are now, or says the organisation has not chosen yet.
- **Own choice**: the group ticks its own calendars. Ticking none means no holidays at all for
  this group.

Choose and press **Save**. When a date has an own exception, that goes first, then a holiday of
a calendar that applies and was not kept open (closed), then the weekly blocks. An
[emergency closure](group-closure.md) is applied on top of all this. A holiday never lifts a
closure.
