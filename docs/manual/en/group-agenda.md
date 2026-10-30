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
of the video meeting and the contact center task. The email address and phone number are only
on a booking's detail page (**Details**).

- **Closed**: an emergency closure covers this slot.
- **Outside the staffing**: only a cancelled or past booking is here; the staffing has no slot
  at this time any more.
- **Direct calls**: calls started without a booking are listed at the bottom.

Use **Previous day**, **Today** and **Next day** to browse. **Week view** opens the week of the
chosen day.

## Looking into a booking

A booking's detail page (**Details**) holds what you need when a video meeting or contact
center task goes wrong:

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

## Changing bookings

With the right **Change bookings** (group admins, organisation admins and a custom Planner
role), the day view shows the links **Move** and **Cancel** next to every booking that can
still be changed. The citizen is always told about a change: by email, and by SMS when they
gave a phone number. You may be asked to log in again first.

A booking can no longer be changed when it starts within the group's **Changes by a planner**
time (60 minutes by default), when it has already gone to the contact center, or when it is
cancelled. Call the citizen instead.

### Move

Choose a free slot of the same team in the next 28 days. The citizen gets a message with the
new time and a new link to change it themselves. If the chosen slot would then have more
bookings than agents, OpenPlanner first asks you to confirm with the box **I accept that the
new slot will then have more bookings than agents**.

### Cancel

Choose one of the fixed reasons: **Not enough agents**, **Technical failure** or **Unforeseen
circumstances**. The citizen reads the matching text in the message. You cannot type a text
yourself; the organisation changes the wording under **Message texts**, in **Reasons for
changes by a planner**.

### Ask for a new time

With **Ask for a new time** at a booking, the citizen chooses a new time themselves. The place
in the slot is freed at once, so the shortfall goes away immediately. The citizen gets a
message with the reason and a link to choose a new time, without entering their details
again. One reminder follows three days before the request lapses. If the citizen chooses
nothing within 14 days after the original time, the appointment lapses; the citizen gets a
short message and the personal data is removed a day later.

A slot with a shortfall also offers **Ask citizens for a new time**. You first see which
bookings it concerns: the most recently made ones, as many as the shortfall. After your
confirmation exactly those citizens get the request. If the slot has changed in the meantime,
OpenPlanner shows the new list and asks you to confirm again.

The bookings awaiting a new time are in the list **Awaiting a new time** in the agenda, with
the original time, the reason and the date the request lapses. There too you can move or cancel
a booking yourself.

### Accept a shortfall

If, for example, a colleague from another team helps out, choose **Accept shortfall** at the
slot. The shortfall then no longer counts in the notices. When the staffing or the bookings of
that slot change afterwards, the shortfall shows again.

Every change is in the audit log with who, which team, the booking number and the reason. When
one person makes many changes in an hour, a system event tells the admins.
