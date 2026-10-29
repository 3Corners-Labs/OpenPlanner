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

### Accept a shortfall

If, for example, a colleague from another team helps out, choose **Accept shortfall** at the
slot. The shortfall then no longer counts in the notices. When the staffing or the bookings of
that slot change afterwards, the shortfall shows again.

Every change is in the audit log with who, which team, the booking number and the reason. When
one person makes many changes in an hour, a system event tells the admins.
