# Group settings

A group is one team that holds video calls with citizens, with its own booking page at
`/g/<web address>`. On this screen you set what those appointments look like and where they
end up. Open it through **Overview → group → Settings**. Only an organisation admin or a
superuser can change settings; before saving, the portal may ask you to log in again.

Changes apply to new appointments. Existing appointments stay as they were booked.

## Web address

When you create a group you may leave the web address empty: the portal then makes it from
the name. What you type yourself is converted to the rules: lower-case letters, digits and
hyphens, no accents, at most 64 characters (`FIN BLD Video group` becomes `fin-bld-video-group`).
If that address is taken, you see the proposed address again and choose another.

The web address (the *slug*) is fixed once the group exists. The organisation's websites link
to it, so a new address would break those links. If you really need another address, create a
new group.

## Planning

<!-- field: name -->
### Name

The group's name as citizens and staff see it: on the booking page, in confirmations and
reminders, and in the contact-centre task. Choose a name a citizen recognises, for example
"Permits" rather than an internal team code.

<!-- field: timezone -->
### Time zone

All of this group's times (blocks, slots, emails) apply in this zone. Summer and winter time
are handled automatically. For the Netherlands that is `Europe/Amsterdam`.

<!-- field: slot_minutes -->
### Length of a call

How long one appointment lasts, in minutes: a multiple of 5, from 5 to 480. The group's blocks
are divided into pieces of this length; each piece is a slot a citizen can choose.

While appointments lie ahead, you cannot change the length, because those appointments would
no longer fit a slot. Wait until they are over, or move them first.

<!-- field: min_notice_minutes -->
### Minimum notice

How long before the start a slot closes for new bookings. With 60, a citizen cannot book
10:30 at 10:00, but can still book 11:00.

This value must be at least **Send to the queue**. An appointment has to be booked before it
goes to the contact centre.

<!-- field: horizon_days -->
### Book ahead

How many days ahead citizens can choose, from 1 to 366. Keep this in line with how far ahead
the team's roster is fixed: a slot that can be booked must also be staffed.

<!-- field: max_bookings_per_email -->
### Appointments per email address

How many future appointments one email address may have with this team at the same time, from
1 to 10. Default 3. People often book for someone else, such as a parent for a child or a carer
for a relative, so do not set the number too low. A cancelled or past appointment does not
count. The same email address twice at the same time is never possible: that is almost always
a double click. A refused citizen gets a general message; the page does not say that an
appointment already exists.

<!-- field: dispatch_kind -->
### How appointments reach staff

- **Contact center**: shortly before the start (see **Send to the queue**) the appointment
  becomes a task in the team's contact-center queue. The first free agent picks it up. This is
  the default.
- **OpenPlanner's own queue**: for teams without a contact center. Staff sign in to OpenPlanner
  and pick up a waiting appointment. The citizen always gets the first free agent. No task goes
  to the contact center, even when the fields below are filled in.

A change applies to appointments not yet dispatched. An appointment already in the contact
center as a task stays there. When the contact center fails (Webex Connect or Webex Contact
Center outage), you can switch the group to the own queue for the time being.

<!-- field: citizen_change_lead_minutes -->
### Citizen can move until (minutes ahead)

Until how many minutes before the start the citizen can move the appointment, 60 by default.
The number is never smaller than **Send to the queue** and at most 1440 (one day). Cancelling
is always possible until the call begins; that is fixed and not a setting. It applies to the
contact center and the own queue. A citizen can only cancel an appointment that was booked
later than this number.

<!-- field: queue_visible_from_minutes -->
### Visible in the queue (minutes ahead, own queue)

Only with the own queue. How many minutes before the start an appointment appears in the
agents' queue, from 0 to 120. Default 15. An appointment stays visible until its end.

<!-- field: queue_free_pick_allowed -->
### Agents may choose an appointment (own queue)

Only with the own queue. **No** (default): agents always pick up the next appointment, so every
participant gets the first free agent. **Yes**: they can also pick up another appointment from
the list. That is a working agreement within the team, not a protection.

<!-- field: calendar_invite_address -->
### Calendar invite to (own queue)

Only with the own queue, and optional. A shared mailbox or mailing list of the team, such as
`team-aangifte@organisatie.nl`. Each appointment sends that address a calendar invite with the
time, the participant's first name, the reference and a link to the queue. When the
appointment is moved, cancelled or put on hold, the invite is withdrawn.

The link to the call is left out on purpose: an agent only gets it by picking up the
appointment. The invite lives in the team's mailbox, outside OpenPlanner's retention. Check
the address carefully; it is also shown on the group's overview page and in the audit log.

<!-- field: dispatch_lead_minutes -->
### Send to the queue

How many minutes before the start the appointment goes to Webex Contact Center as a task, from
0 to 60. The citizen can still cancel after that through the link in the confirmation; moving is
possible until **Citizen can move until**.

Choose enough time for an agent to pick up the task before the citizen waits in the video call,
for example 5 minutes.

<!-- field: planner_change_min_lead_minutes -->
### Changes by a planner

Up to how many minutes before the start a planner may move or cancel a booking from the
[agenda](group-agenda.md), 60 by default. After that a message would not reach the citizen in
time: call the citizen instead. The number is never less than **Send to the queue** and at most
1440 (one day).

<!-- field: phone_mode -->
### Phone number on the form

Whether the booking form asks for a phone number:

| Choice | Meaning |
|---|---|
| Not asked | The form has no phone field. |
| Optional | The citizen may give a number, for example for a text-message reminder. |
| Required | The citizen cannot book without a number. |

Only ask for a number if the team actually uses it. Like the name and email address, the
number is erased 24 hours after the appointment.

## Integrations: video and contact centre

Leave these fields empty while the integration is not set up. Appointments then still work, but
without a video meeting or a contact-centre task. The values come from the administrator of
Webex and Webex Contact Center.

<!-- field: video_host_email -->
### Webex host

The email address of a licensed Webex account. The video meetings are scheduled on this
account. Staff who log in with an organisation account become cohost automatically, so they can
run the call without the host being present.

Use a functional account (for example `videocalls@municipality.nl`), not a personal one: it
must keep working when someone leaves.

As soon as you save a new address, OpenPlanner checks the integration right away, just like the
**Check integration** button at the bottom of the page: a probe meeting is scheduled and removed
again. The result is shown under **Check integration** after saving. From then on the platform
checks every group with a host by itself, once a day by default. When something is wrong (no
licence, wrong account, missing rights), the group's audit log shows a system entry
*Integration checked* with Webex's answer under it; it repeats until it is fixed and reports
once that all is well again. So you hear about it before a citizen gets an appointment without
a video call.

The check also looks at the **background jobs (worker)**: the part of OpenPlanner that really
creates the video meetings. The probe meeting runs in the admin portal, bookings run in the
worker. The check reports whether the worker runs and which version, whether it runs the same
version as the admin portal (if not, an update is half way), whether it was started with the
Webex settings, and how many jobs of this group failed or wait too long. A job waits too long
once it is more than the platform setting *Job overdue after* (default 10 minutes) past its time.

<!-- field: wxcc_business_address -->
### WxCC business address

The address of the *digital channel asset* in Webex Contact Center that receives this group's
tasks. Several groups can share an address; the routing value then picks the queue.

<!-- field: wxcc_channel -->
### WxCC work item channel

The name of the work item channel in Webex Contact Center, as the contact-centre administrator
set it up.

<!-- field: wxcc_routing_value -->
### Routing value for the WxCC flow

The value the flow in Webex Contact Center uses to choose the queue. If you leave it empty, the
portal sends the group's web address. Only fill in something else if the flow expects another
value.

<!-- field: trial_call_enabled -->
### Trial call for the citizen

With **Yes** the citizen can try, before the appointment and in a real Webex meeting, whether
camera, microphone, speakers and browser work. It is not a separate test on our page: it is the
same Webex site and the same Webex environment as the call itself, so a problem shows up now.
The citizen is alone in it; nobody from the team joins and no task is created. The link is in
the confirmation, the reminder, on the appointment page and on the page for the call. It only
works with a **Webex host**. The trial call uses the host's Webex site, which must allow
participants to enter before the host. **Check the integration** shows whether it does.

<!-- field: trial_call_minutes -->
### Trial call stays open (minutes)

How long one trial call stays open, from 10 to 60 minutes. Default 30. After that the Webex
meeting is deleted; the citizen can open a new trial call.

<!-- field: trial_calls_per_booking -->
### Trial calls per appointment

How often one appointment may open a trial call, from 1 to 5. Default 3. Each trial call is a
meeting in Webex; this number stops one appointment from creating them without end.

<!-- field: direct_call_enabled -->
### Start a call right away

With **Yes**, citizens can start a video call right away during a block with agents, without an
appointment. The booking page then shows a button. Outside the blocks, on holidays or during an
emergency closure, the button disappears by itself.

<!-- field: not_picked_up_minutes -->
### Not picked up after

How long a call may wait without an agent picking it up, from 1 to 120 minutes. After that it
is flagged in the overviews. A direct call that has waited this long also no longer counts
towards the maximum below.

<!-- field: direct_call_max_concurrent -->
### Maximum number of direct calls at once

How many direct calls may be open at once: waiting, or in a call with an agent. Usually that is
the number of agents who take direct calls. When the maximum is reached, the citizen sees that
all agents are busy, with the choice to try again later or to book an appointment.

Empty means no limit: Webex Contact Center then distributes every call. Scheduled appointments
do not count; the slots already limit those.

<!-- field: embedding_enabled -->
### Embed on websites

With **Yes**, the group can also appear as a widget on the organisation's website, next to the
group's own page. This works only on the websites the organisation allows under
[Embedding on websites](embedding.md). With **No** (the default), the group can only be booked
on its own page, even when the widget is on an allowed website.
