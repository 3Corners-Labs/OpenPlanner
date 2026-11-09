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
  system, and not confirmed in time. *Completed* and *no show* are worked out two hours after the
  appointment from the video call: a participant who was seen in the meeting with the host is
  completed, one who was never seen in a meeting that was set up is a no show. Where the platform
  does not report who joined, an appointment is counted as neither.
- **How far ahead bookings are made**: the time between making the booking and its appointment,
  in fixed ranges, with the mean. Times are kept to the quarter hour.
- **Cancellations by participants**: how much time was left before the appointment.
- **Rescheduled bookings**: by whom they were moved.
- **Confirmation by email**: bookings that were confirmed against those that lapsed, with the
  percentage confirmed.
- **Emergency closures**: bookings put on hold and confirmed again.
- **Bookings from before reporting**: outcomes of bookings that were merged into daily counts before
  usage was counted; nothing more is known about them.

## The report families

At the top of the Usage page a row of links switches between four families of the same report. The
period you chose stays; every family has a URL you can bookmark. The first, **Volume**, is the set
of tables above; the others are below.

### Queue and calls

How bookings reach staff and how the calls go.

- **How bookings reach staff**: bookings by queue method (contact center or the own queue) and by
  kind (scheduled or direct call), with totals.
- **Wait until a call connects** (Q2): the time from a task being made to the call connecting, in
  fixed ranges, with the mean.
- **When the host joined, against the start** (Q3): early is shown with *early*, late with *late*.
  Only scheduled calls count.
- **Participant waiting for the host** (Q4) and **how long calls last** (Q5), with the mean, the
  number of finished calls and the mean number of people per call (U11).
- **Direct calls** (Q6): started, left while waiting, not picked up in time, and the share that
  left, with the wait at the moment of leaving.
- **Own queue: claims** (Q7): claimed, given back, given back per claim, and the time until a
  booking is claimed. The share of bookings that were claimed is not shown: what was offered is
  not recorded.
- **Dispatch failures** (Q8): per code from a fixed list, never the text of the other system.
- **Direct calls at once** (Q9): the highest number waiting or connected at once, per day.
- **New times proposed** (U8): proposed after a planner asked or by the assistant, accepted,
  lapsed, the share accepted and the time until accepting.

### Capacity

How full the agenda is and when it is busy.

- **Utilisation per day** (C1, C3): bookings that count against the capacity of the slots that
  have started, and the places nobody booked. A day with few slots is hidden, and so is a
  percentage that stands for only a few people.
- **How full the slots were** (C2): empty, under half, more than half and full.
- **Busiest days and hours** (C4): a table of weekday by hour, in the group's own time. The number
  is in every cell; the shade only helps the eye. It is shown for a period of at least 7 days and
  only for groups with at least five times the minimum in bookings in that period; smaller groups
  are left out of this table, and it says how many.
- **Blocks short of staff** (C5): still short, short and accepted by a planner, resolved.

### Channels

How messages arrive, in which language people book, and how the trial call goes.

- **Messages sent and failed** (M1): per purpose and per channel, with the share that failed.
- **Bookings per language** (M2): only for groups big enough; smaller groups are left out and the
  page says how many.
- **Trial calls** (T1): made, followed the link, entered the meeting and failed. The gap between
  the last two is people who got stuck in the lobby.

## Charts

Where a chart helps, it sits above its own table and the table always has the same numbers, so
nothing is only in the picture. A chart has a title and a description for screen readers, series
differ by pattern or line style as well as by colour, and a count that is hidden is a dashed marker
of one fixed height labelled *< 5*: it says nothing about the real number. The charts need no
script, scroll sideways inside their frame on a narrow screen, and are left out when printing; the
tables remain. When nothing has been recorded for a family in the period, the page says so.

## Downloading as a file

Whoever may export (the superuser, organisation admins, group admins and the Compliance and privacy role) sees two links under the period on a Usage page.

- **Download this report as CSV** gives exactly the tables of the family you are looking at, after the small-count rule: a hidden figure is an empty value with the note *hidden*. The file has no text of the interface, only codes (a day, an outcome, a bucket number), so it reads the same in every language.
- **Download all hourly figures as CSV** gives one row per hour, group, metric and cut, for analysis in your own tool (the format `openplanner.usage/1`, described in `docs/reporting.md`). A cell below the minimum is not in the file at all.

Both open in Excel (UTF-8, `;` as separator). The platform administrator sets how many rows and days one file may hold; a longer period is refused with a message, and the file is not cut short. Every download is recorded in the audit log with who, which family, which period, how many rows and a checksum of the file, never the figures. The checksum is also in the `X-Content-SHA256` header of the response.

## The digest by email

Organisation admins see a section **Digest by email** at the bottom of the organisation's *Usage* page. A subscription is for the whole organisation or for one group and has a period: every day, every week (on a weekday) or every month (on a day from 1 to 28). The mail goes out at the organisation's send hour (7:00 unless changed) in the group's time zone, covers the days since the last mail up to and including yesterday, and holds the main figures (bookings created, no shows, the mean wait until a call connects, utilisation) and a link to the report of those days. A hidden figure shows a dash. If your organisation switches the figures off, only the link remains.

Recipients are colleagues who hold the right to see the reports for that scope, chosen from a list. There are no free email addresses and no file goes with the mail: the link leads to the report, where you sign in as usual. The right is checked again when the mail goes out. A colleague who lost it is skipped and the list says how many; a colleague whose address the sign-in provider does not know is skipped too (the addresses come from the bundled provider). A missed mail is still sent for up to 24 hours; later it is skipped, and the list shows how the last mail went. Every change to a subscription is in the audit log.

## What is not here yet

Per team member. That comes in a later step of the same design.
