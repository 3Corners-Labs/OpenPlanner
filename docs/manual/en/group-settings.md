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

<!-- field: dispatch_lead_minutes -->
### Send to the queue

How many minutes before the start the appointment goes to Webex Contact Center as a task, from
0 to 60. From that moment the citizen can no longer change or cancel the appointment through the
link in the confirmation.

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
