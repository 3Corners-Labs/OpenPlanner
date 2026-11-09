# Group agenda

The agenda shows, per slot, how many agents are on duty and how many bookings there are. Open
it from a group's **Agenda** tab. Everyone who may view the group's bookings can open it:
group admins, organisation admins and the superuser, and a custom role with the right to view
bookings.

The number of agents comes from the group's staffing: the weekly blocks, the exceptions, the
holidays and an emergency closure. When you change the staffing, the agenda shows it at once.

## Day view

Every slot of the day has its own block, with its time, the number of agents and the number of
confirmed bookings. Below it are the bookings with the participant's first name and the state
of the video meeting (with the Webex meeting ID under it) and the contact center task. The full
name, email address and phone number are only on a booking's detail page (**Details**), and
there too only when you ask for them (see *Reaching the participant*).

- **Closed**: an emergency closure covers this slot.
- **Outside the staffing**: only a cancelled or past booking is here; the staffing has no slot
  at this time any more.
- **Direct calls**: calls started without a booking are listed at the bottom.

Use **Previous day**, **Today** and **Next day** to browse. **Week view** opens the week of the
chosen day.

### The meetings' state stays current

The **Meeting**, **Agent** and **Participants** columns refresh themselves without reloading
the page. A screen reader hears one short sentence ("Status updated: 1 calls in progress, 0
not picked up"); focus only moves if it was inside the table. Today and in the future this
happens every 30 seconds; a day in the past no longer changes and does not refresh. The
superuser sets the 30 seconds in the platform settings (**Refresh of meeting status on the
agenda**, 10 to 300 seconds).

The state comes from Webex first: Webex sends an event when a meeting starts or ends or when
someone joins or leaves. If such an event does not arrive, OpenPlanner asks Webex for the state
once it is older than the refresh time, and only for bookings that can have a running meeting:
confirmed, with a meeting, from 15 minutes before the start to 30 minutes after the end. A
missed event is therefore corrected within about half a minute. In **Timeline** such an event
carries the addition *polled*.

When Webex does not answer, the agenda stays as it was. The booking's detail page shows an
*Integration failed* event under **Timeline** with its code (for example `polled:rejected_403`)
and, under *Webex's answer*, the reason Webex itself gave. It appears once per kind of
failure, not on every refresh.

If your browser runs without scripts, the agenda shows its state as loaded and the **Refresh**
link reloads it.

## Looking into a booking

A booking's detail page (**Details**) holds what you need when a video meeting or contact
center task goes wrong:

- **Meeting ID**: the Webex meeting's own ID, the one to quote to Webex support. It stays on
  the page when a participant stopped waiting for a direct call: OpenPlanner keeps following the
  meeting while the agent is still in it, and the place of that call stays taken until Webex
  reports the meeting's end.
- **Timeline**: what happened to the meeting and the task. Each event shows the version of
  OpenPlanner that wrote it, so on a test environment that updates often you can tell an error
  of an older version. *Version unknown* means it was written before OpenPlanner kept it.
- **Background jobs**: every job of the booking (video meeting, contact center task, messages,
  removing personal data) with its planned time, state, attempts and error code. A failed video
  meeting or contact center task has a **Try again** button, shown to those who may change the
  group's bookings. The worker runs the job within a minute; reload the page for the outcome. A
  job that had nothing to do (the meeting existed, or the group uses the own queue) shows in the
  audit log as *Skipped* with its reason, not as succeeded.
- **Requests to Webex and Webex Contact Center**: every call OpenPlanner made for the booking,
  with the answer (status code), duration, *tracking id* and version. Give the tracking id to
  Cisco support when Webex has to look into the error. It holds no personal data. These
  requests are kept for 30 days by default.

## Reaching the participant

The detail page does not simply show the participant's contact details: you see them only after
you choose **Show contact details**. It then shows the first and last name, the email address
and the phone number, if the participant gave one (otherwise it says *Not given*). Clicking the
number or the address calls or mails from your own program.

- Show them only when you need to reach the participant, for example when a booking can no
  longer be changed (see *Changing bookings*). Whoever opens the page to look into a failure
  does not see them.
- Each time you show them, the audit log records who did, for which group and which booking
  (*Participant's contact details shown*). The details themselves are not in it.
- When one person shows contact details more often within an hour than the platform's limit
  (20 times by default), one warning goes into the audit log: *Many requests for contact
  details by one person*, naming who it was and how often. The same booking again counts too.
  The details still show; the superuser sets the limit (see
  [Advanced settings](advanced-settings.md)).
- **Hide contact details** takes them off the screen again, for example before you take a
  screenshot for Webex support.
- Only the button on the page itself shows them. When you open the page through a shared link
  (in a mail or chat), a bookmark or a link on another website or page, they are not shown and
  nothing goes into the audit log; choose **Show contact details** yourself. If the button
  does not show them either, your browser or the network does not pass on which page you came
  from (the *referrer*); ask your administrator to allow it for the portal.
- When the booking's personal data have been deleted (see
  [Data fields and personal data](data-fields.md)), the page says so and there is nothing left
  to show.

Everyone who may view the group's bookings can show them. The Compliance and privacy role never
can, and agents in the queue see the first name only.

## Week view

A table with the times in the rows and the days from Monday to Sunday in the columns. Each cell
gives the number of agents and bookings and opens that slot in the day view. A dash means there
is no slot on that day. The week view shows counts only, no names.

## Shortfalls

A slot with more confirmed bookings than agents has a **shortfall**. This happens when you
lower the staffing after people have booked: bookings always stay. A shortfall is written out
(**Short: 1**) with a warning sign, never shown by colour alone.

The group's overview, the **Staffing**, **Staffing exceptions** and **Holidays** pages and the dashboard
say how many slots in the next 14 days are short, with a link to the first one. So right after
a staffing change you see whether it causes a shortfall.

Solve a shortfall by raising the staffing again, or by changing bookings (below).

## Unconfirmed appointments

A new appointment waits until the participant confirms it with the link in the email. The day
view and the appointment itself then show **Not confirmed, at the latest** with the moment the
appointment lapses if nobody confirms. Such an appointment holds its place: the slot says how many
of its appointments are not confirmed yet, and they count towards shortfalls. **Not confirmed
only** shows just these appointments; **All appointments** shows everything again.

When the participant calls, someone with the right **Change bookings** can choose **Confirm for
participant**. The participant then gets the same confirmation with all details as after
confirming themselves, and the audit log records who confirmed. After the lapse moment that is no
longer possible.

An unconfirmed appointment can also be **Moved** or **Cancelled** like a confirmed one. A moved
appointment stays unconfirmed and gets a new lapse moment. Asking for a new time is not offered:
such an appointment lapses on its own when nobody confirms.

## Changing bookings

With the right **Change bookings** (group admins, organisation admins and a custom Planner
role), the day view shows the links **Move** and **Cancel** next to every booking that can
still be changed. The participant is always told about a change: by email, and by SMS when they
gave a phone number. You may be asked to log in again first.

A booking can no longer be changed when it starts within the group's **Changes by a planner**
time (60 minutes by default), when it has already gone to the contact center, or when it is
cancelled. Contact the participant yourself instead: choose **Details** at the booking, then
**Show contact details** (see *Reaching the participant*).

### Move

Choose a free slot of the same team in the next 28 days. The participant gets a message with the
new time and a new link to change it themselves. If the chosen slot would then have more
bookings than agents, OpenPlanner first asks you to confirm with the box **I accept that the
new slot will then have more bookings than agents**.

### Cancel

Choose one of the fixed reasons: **Not enough agents**, **Technical failure** or **Unforeseen
circumstances**. The participant reads the matching text in the message. You cannot type a text
yourself; the organisation changes the wording under **Message texts**, in **Reasons for
changes by a planner**.

### Ask for a new time

With **Ask for a new time** at a booking, the participant chooses a new time themselves. The place
in the slot is freed at once, so the shortfall goes away immediately. The participant gets a
message with the reason and a link to choose a new time, without entering their details
again. One reminder follows three days before the request lapses. If the participant chooses
nothing within 14 days after the original time, the appointment lapses; the participant gets a
short message and the personal data is removed a day later.

A slot with a shortfall also offers **Ask participants for a new time**. You first see which
bookings it concerns: the most recently made ones, as many as the shortfall. After your
confirmation exactly those participants get the request. If the slot has changed in the meantime,
OpenPlanner shows the new list and asks you to confirm again.

The bookings awaiting a new time are in the list **Awaiting a new time** in the agenda, with
the original time, the reason and the date the request lapses. There too you can move or cancel
a booking yourself.

### Propose a new time

With **Propose a new time** on a confirmed appointment the assistant looks for a free time for
the participant: the same time of day on another day first, then the smallest difference in time
of day, then the nearest day. You can keep that time or choose another free one. The old place is
free at once; the proposed time is held for the participant and counts in the agenda as not
confirmed. The participant gets the proposal by email (and SMS when there is a phone number) and
can confirm the new time, choose another time or cancel. Nothing moves without a confirmation:
after the deadline the held time is released and the participant can still choose until the
request lapses.

A slot with a shortfall also offers **Propose new times**. For each appointment (the most
recently made, as many as the shortfall) you see the proposed time. For each you can choose
another time, let the participant choose, or untick it. Everything goes at once; when a chosen
time no longer fits, OpenPlanner sends nothing and shows the list again. With **Propose new times
for this day** (day view) or **for this week** (week view) you review every slot with a shortfall
at once; the assistant never gives two appointments the same last place. OpenPlanner never
proposes a time without a free agent.

In **Awaiting a new time** a proposal shows the proposed time and until when it is held. After a
phone call you can choose **Confirm for the participant** there; the participant then gets the
confirmation with the new time. **Withdraw the proposal** releases the held time and the
appointment keeps awaiting a time from the participant.

How long a proposal is held, when its reminder goes and how far the assistant looks are set by
the organisation's administrator in the settings (topic Planner).

### Automatic proposals

A group can let the assistant send its proposals by itself (group settings, **Send proposals
automatically**). It then looks at the slots with a shortfall every few minutes. A shortfall that
stays exactly the same for the group's waiting time gets proposals for the most recently made
appointments, with the reason *staffing*; one that changes or disappears starts waiting again.
When more appointments are affected than the group's ceiling, nothing is sent and the
planners are told once; the shortfall stays in the agenda for you to review as usual.

### Accept a shortfall

If, for example, a colleague from another team helps out, choose **Accept shortfall** at the
slot. The shortfall then no longer counts in the notices. When the staffing or the bookings of
that slot change afterwards, the shortfall shows again.

Every change is in the audit log with who, which team, the booking number and the reason. When
one person makes many changes in an hour, a system event tells the admins.
