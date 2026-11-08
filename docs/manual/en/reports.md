# Reports

The reports show how your groups are used: how many bookings are made, how they end, and how far
ahead people book. You open the list with **Reports** in the menu, and a group's or an
organisation's figures with the **Usage** tab under its *Overview*. Organisation admins, group
admins and the Compliance and privacy role can view them, each for what they may see; the
superuser sees everything. A working agent does not have reports.

The reports hold counts only. They never show a name, an address, a phone number or which booking
a figure comes from, and they are not made from the appointments: they are counted when a booking
changes, one small record per change.

## Reading a table

- **Days** are whole days in the group's own time zone. Choose a range with **From** and **To**;
  without a choice you see the last four weeks. The most recent day is marked *provisional*: how
  its bookings end is not final yet.
- **Figures as of** says when the counts were last brought up to date. If that is long ago, System
  status warns about it.
- **A count that is hidden** is written as *< 5*. A count below the minimum could point to one
  person, so it is not shown, and neither is the total of a table when it would give a hidden count
  back by subtraction. This is not a mistake and it cannot be switched off: the platform
  administrator sets the minimum (never below 3), and your organisation may raise it for its own
  reports, never lower it.

## The tables

- **Bookings created per day**: new bookings, without those made by moving another one.
- **How bookings ended**: cancelled by the participant, by the planner, after a closure or by the
  system, and not confirmed in time. *Completed* and *no show* stay empty for now: nothing in the
  service records them yet.
- **How far ahead bookings are made**: the time between making the booking and its appointment,
  in fixed ranges, with the mean. Times are kept to the quarter hour.
- **Cancellations by participants**: how much time was left before the appointment.
- **Rescheduled bookings**: by whom they were moved.
- **Confirmation by email**: bookings that were confirmed against those that lapsed, with the
  percentage confirmed.
- **Emergency closures**: bookings put on hold and confirmed again.
- **Bookings from before reporting**: outcomes of bookings that were merged into daily counts before
  usage was counted; nothing more is known about them.

## What is not here yet

Waiting times, call lengths, how full the agenda is, the busiest hours, messages and per team
member. Those come in later steps of the same design.
