# Group settings

A group is one team that holds video calls with participants, with its own booking page at
`/g/<web address>`. On this screen you set what those appointments look like and where they
end up. Open it through **Overview → group → Settings**. Only an organisation admin or a
superuser can change settings; before saving, the portal may ask you to log in again.

Changes apply to new appointments. Existing appointments stay as they were booked.

At the top, *On this page* lists every topic of this screen (general, integrations, identity provider, booking form, check and archiving) so you can jump straight to it. You only see archiving if you may archive a group.

## Web address

When you create a group you may leave the web address empty: the portal then makes it from
the name. What you type yourself is converted to the rules: lower-case letters, digits and
hyphens, no accents, at most 64 characters (`Finance Video group` becomes `finance-video-group`).
If that address is taken, you see the proposed address again and choose another.

The web address (the *slug*) is fixed once the group exists. The organisation's websites link
to it, so a new address would break those links. If you really need another address, create a
new group.

## Planning

<!-- field: name -->
### Name

The group's name as participants and staff see it: on the booking page, in confirmations and
reminders, and in the contact-centre task. Choose a name a participant recognises, for example
"Customer service" rather than an internal team code.

<!-- field: timezone -->
### Time zone

All of this group's times (blocks, slots, emails) apply in this zone. Summer and winter time
are handled automatically. For the Netherlands that is `Europe/Amsterdam`.

<!-- field: slot_minutes -->
### Length of a call

How long one appointment lasts, in minutes: a multiple of 5, from 5 to 480. The group's blocks
are divided into pieces of this length; each piece is a slot a participant can choose.

While appointments lie ahead, you cannot change the length, because those appointments would
no longer fit a slot. Wait until they are over, or move them first.

<!-- field: min_notice_minutes -->
### Minimum notice

How long before the start a slot closes for new bookings. With 60, a participant cannot book
10:30 at 10:00, but can still book 11:00.

This value must be at least **Send to the queue**. An appointment has to be booked before it
goes to the contact centre.

<!-- field: horizon_days -->
### Book ahead

How many days ahead participants can choose, from 1 to 366. Keep this in line with how far ahead
the team's roster is fixed: a slot that can be booked must also be staffed.

<!-- field: max_bookings_per_email -->
### Appointments per email address

How many future appointments one email address may have with this team at the same time, from
1 to 10. Default 3. People often book for someone else, such as a parent for a child or a carer
for a relative, so do not set the number too low. A cancelled or past appointment does not
count. The same email address twice at the same time is never possible: that is almost always
a double click. The page shows a refused participant the same "check your email" page as
anyone else; only the email to that address explains why the appointment could not be made.
A refused attempt does not appear in the agenda.

<!-- field: reschedule_auto_send -->
### Send proposals automatically

Off by default. When there are too few agents for the bookings of a slot, the assistant can
propose new times to the most recently made appointments by itself, without a planner reviewing
them first. Switch it on only for a team that agrees to that. The participant always confirms,
chooses another time or cancels: nothing moves without them. Every automatic proposal is in the
audit log with the system as actor.

<!-- field: reschedule_auto_delay_minutes -->
### Wait before automatic proposals

How long a shortfall must stay exactly the same before the proposals go out, from 5 to 120
minutes. Default 15. This gives you time to correct a typing error in the staffing: when the
shortfall changes or disappears, the waiting starts again.

<!-- field: reschedule_auto_max_bookings -->
### Most appointments for automatic sending

From 1 to 100, default 10. When more appointments are affected, the assistant sends nothing and
asks a planner to review the proposals on the agenda; the planners are told once. This keeps a
mistake in the staffing from moving a whole day.

<!-- field: dispatch_kind -->
### How appointments reach staff

- **Contact center**: shortly before the start (see **Send to the queue**) the appointment
  becomes a task in the team's contact-center queue. The first free agent picks it up. This is
  the default.
- **OpenPlanner's own queue**: for teams without a contact center. Staff sign in to OpenPlanner
  and pick up a waiting appointment. The participant always gets the first free agent. No task goes
  to the contact center, even when the fields below are filled in.

A change applies to appointments not yet dispatched. An appointment already in the contact
center as a task stays there. When the contact center fails (Webex Connect or Webex Contact
Center outage), you can switch the group to the own queue for the time being.

<!-- field: dispatch_fallback -->
### Fallback when the contact center fails

Only for a group with **Contact center**. With **Own queue** an appointment goes to OpenPlanner's
queue, flagged **Fallback**, when:

- its dispatch fails for good (after the retries), or
- the organisation's contact center is down (see *Health* on Integrations): the whole group then
  falls back. The administrators get an alert, the queue shows a banner, and every appointment in
  the agenda shows **Fallback**.

When the contact center is fine again, the fallback ends by itself and appointments not yet
picked up go to the contact center after all. Whoever manages the group's appointments can also
end the fallback in the queue. The participant notices nothing: the link, the lobby and the rules
for cancelling and moving stay the same.

Choose this only if the team knows the queue and can work it. What the team sees and how it
ends is in [Setting up the contact center](contact-center-setup.md), step 10.

<!-- field: fallback_space_room_id -->
### The team's Webex space (fallback)

Optional. The id of the team's Webex space. During a fallback the organisation's bot (see *Alerts
to administrators* on Channels) posts a message there, and for each appointment that lands in the
queue the start time, the first name and a link to the queue. Never the link to the call: only
whoever picks the appointment up gets that. Those messages stay in Webex after OpenPlanner has
deleted the appointment's data; see [Setting up the contact center](contact-center-setup.md),
steps 10 and 11.

<!-- field: participant_change_lead_minutes -->
### Participant can move until (minutes ahead)

Until how many minutes before the start the participant can move the appointment, 60 by default.
The number is never smaller than **Send to the queue** and at most 1440 (one day). Cancelling
is always possible until the call begins; that is fixed and not a setting. It applies to the
contact center and the own queue. A participant can only cancel an appointment that was booked
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
`team-balie@example.org`. Each appointment sends that address a calendar invite with the
time, the participant's first name, the reference and a link to the queue. When the
appointment is moved, cancelled or put on hold, the invite is withdrawn.

The link to the call is left out on purpose: an agent only gets it by picking up the
appointment. The invite lives in the team's mailbox, outside OpenPlanner's retention. Check
the address carefully; it is also shown on the group's overview page and in the audit log.

<!-- field: dispatch_lead_minutes -->
### Send to the queue

How many minutes before the start the appointment goes to Webex Contact Center as a task, from
0 to 60. The participant can still cancel after that through the link in the confirmation; moving is
possible until **Participant can move until**.

Choose enough time for an agent to pick up the task before the participant waits in the video call,
for example 5 minutes.

<!-- field: planner_change_min_lead_minutes -->
### Changes by a planner

Up to how many minutes before the start a planner may move or cancel a booking from the
[agenda](group-agenda.md), 60 by default. After that a message would not reach the participant in
time: call the participant instead. The number is never less than **Send to the queue** and at most
1440 (one day).

<!-- field: phone_mode -->
### Phone number on the form

Whether the booking form asks for a phone number:

| Choice | Meaning |
|---|---|
| Not asked | The form has no phone field. |
| Optional | The participant may give a number, for example for a text-message reminder. |
| Required | The participant cannot book without a number. |

Only ask for a number if the team actually uses it. Like the name and email address, the
number is erased 24 hours after the appointment.

## Subjects and routing

Let participants choose a subject, ask for a preferred language or way of contact, and pass those
choices to the contact center as codes. You set that up on a screen of its own: see
[Subjects and routing](group-routing.md).

## Integrations: video and contact centre

Leave these fields empty while the integration is not set up. Appointments then still work, but
without a video meeting or a contact-centre task. The values come from the administrator of
Webex and Webex Contact Center. The whole set-up, step by step, is in
[Setting up the contact center](contact-center-setup.md).

<!-- field: video_host_email -->
### Webex host

The email address of a licensed Webex account. The video meetings are scheduled on this
account. Staff who log in with an organisation account become cohost automatically, so they can
run the call without the host being present.

Use a functional account (for example `videocalls@municipality.nl`), not a personal one: it
must keep working when someone leaves.

As soon as you save a new address, OpenPlanner checks the integration right away, just like the
**Check integration** button at the bottom of the page: a probe meeting is scheduled and removed
again, and for a group with a contact-center mapping a probe work item goes through the flow
(see *WxCC business address*). The result is shown under **Check integration** after saving.
From then on the platform checks every group with a host or a contact-center mapping by
itself, once a day by default. When something is wrong (no
licence, wrong account, missing rights), the group's audit log shows a system entry
*Integration checked* with Webex's answer under it; it repeats until it is fixed and reports
once that all is well again. So you hear about it before a participant gets an appointment without
a video call.

The check also looks at the **background jobs (worker)**: the part of OpenPlanner that really
creates the video meetings. The probe meeting runs in the admin portal, bookings run in the
worker. The check reports whether the worker runs and which version, whether it runs the same
version as the admin portal (if not, an update is half way), whether it was started with the
Webex and the WxCC settings, and how many jobs of this group failed or wait too long. A job waits too long
once it is more than the platform setting *Job overdue after* (default 10 minutes) past its time.

<!-- field: wxcc_business_address -->
### WxCC business address

**Check integration** sends a **probe work item** through Webex Contact Center with these
fields, exactly as a booking does: with kind `probe`, a reference that belongs to no booking
and the first name "Probe", so without any participant data. It then waits until WxCC reports
the creation and the end (60 seconds by default). That proves token, rights, asset, flow,
subscription and webhook in one go. The flow should have a branch for kind `probe` that ends
the item at once; without it OpenPlanner ends the item itself and shows a warning. When no
event comes back, the result names what is missing (subscription, webhook or the branch in
the flow).

The address of the *digital channel asset* in Webex Contact Center that receives this group's
tasks. Several groups can share an address; the routing value then picks the queue. How to set
up the asset: [Setting up the contact center](contact-center-setup.md), step 3.

<!-- field: wxcc_control_hub -->
### Where to find it in Control Hub

The check uses OpenPlanner's names; this is where each part lives in Control Hub
(admin.webex.com > **Contact Center**). Flows are edited in **Flow Designer**, which opens from
the flow.

| In the check | In Control Hub | What to look at |
|---|---|---|
| Region | the URL of Contact Center, or **Settings** > *Data location* | must match OpenPlanner's region |
| Work item channel | **Digital settings** > **Custom Channels** | *Channel type* Work item; the name is the group's *WxCC work item channel* |
| Asset, business address | **Digital settings** > **Assets** | *Channel*, *Business address* (the group's *WxCC business address*) and **Data schema** |
| Entry point | **Customer experience** > **Channels** | the channel of type Work item that points at the asset; *Status* active, a *Routing flow* chosen |
| Flow | **Customer experience** > **Flows** > the routing flow | *Flow variables*, **Case**, **Queue Contact**, **Screen Pop**, **End Flow**; publish after a change |
| Queue | **Customer experience** > **Queues** | *Channel type* Work item, and agents' **Multimedia profiles** (*User management*) allow work items |
| Service App, scopes | developer.webex.com > *My Webex Apps*; authorised in Control Hub > **Apps** > **Service Apps** | scopes `cjp:config_read`, `cjp:config_write`, `cjp:user` |
| Subscription | not in Control Hub; only through the API | `register-webhooks` on the server creates it |

<!-- field: wxcc_asset_schema -->
### Data schema of the WxCC asset

Webex Contact Center only accepts a work item from OpenPlanner when the *Data schema* of the
asset has exactly these fields. When **Check integration** says the work item is refused, ask
the contact-centre administrator to change the asset in Control Hub: **Contact Center** >
**Digital settings** > **Assets** > the asset with this group's business address > **Data schema**. Per field, use
the pencil to change it and **Add a field** for a field that is missing.

| Field key | Display name (free) | Viewable | Required |
|---|---|---|---|
| `bookingReference` | Afspraak | Yes | **Yes** |
| `firstName` | Voornaam | Yes | No |
| `startTime` | Starttijd | Yes | No |
| `groupName` | Team | Yes | No |
| `meetingLink` | Videogesprek | Yes | No |
| `kind` | Soort | Yes | No |

Type the field keys exactly, including capitals. Control Hub wants at least one required
field: that is `bookingReference`, and only that one, because a cancellation sends
nothing else. Any other required field, also one OpenPlanner sends, makes Webex Contact Center
refuse those work items. Remove fields of your own or set them to Required No. Save, then click
**Check integration** again.

<!-- field: wxcc_flow -->
### The WxCC flow for this group

**Check integration** reads the flow behind the asset's entry point and warns about three parts
the contact-centre administrator adds in Flow Designer:

1. **Routing to the right queue.** Add a flow variable `openplannerGroup` (type String, *Make
   available in Desktop* not needed). OpenPlanner fills it with the group's *Routing value*. A
   **Case** on `openplannerGroup` with one branch per routing value, each to a **Queue Contact**
   with that group's queue. Without it every work item of the entry point goes to one queue,
   which is fine while only one group uses the asset.
2. **A branch for the probe.** OpenPlanner's check sends a work item with `kind` = `probe`. Put
   a **Case** on `kind` before the queue, with a branch `probe` that goes straight to **End
   Flow** (or Disconnect Contact). Without it OpenPlanner ends the probe itself, and an agent
   may see it for a moment.
3. **Screen Pop.** Add a **Screen Pop** activity with the URL `meetingLink`, so the agent opens
   the video call in one click.

Publish the flow, then click **Check integration** again.

<!-- field: wxcc_events -->
### Events from WxCC back to OpenPlanner

OpenPlanner hears that a work item arrived, was accepted or ended through a **subscription** to
task events at Webex Contact Center, which calls OpenPlanner's webhook address. When the probe
was created but no *task:new* came back, look at the **Webhooks** part of the same check:

- *There is no active subscription*: the platform administrator runs `register-webhooks` on the
  server (runbook, section 4). The Service App needs the scope `cjp:config_write` for it. WxCC
  switches a subscription off after repeated failed deliveries, so run it again after an
  outage or an address change.
- *Test message to wxcc* failed: the webhook address (System status, *Webhook address*) is
  not reachable from the internet, or the proxy refuses the request. Fix the address or the
  proxy, then run `register-webhooks` again.
- Both OK and still no event: the subscription is for another organisation or region. Check
  that the region line of this check is OK and run `register-webhooks` again.

<!-- field: wxcc_channel -->
### WxCC work item channel

The name of the work item channel in Webex Contact Center, as the contact-centre administrator
set it up ([Setting up the contact center](contact-center-setup.md), step 2).

<!-- field: wxcc_routing_value -->
### Routing value for the WxCC flow

The value the flow in Webex Contact Center uses to choose the queue. If you leave it empty, the
portal sends the group's web address. Only fill in something else if the flow expects another
value.

<!-- field: wxcc_sample_flow -->
### Sample flow to import

Instead of building the flow by hand, you can generate one. A host administrator runs
`python -m app.cli wxcc-sample-flow --group <web address>` (add `--queue VALUE=QUEUE` for each
routing value and `--fallback-queue` for the default branch). The result is a file for **Control
Hub → Contact Center → Customer experience → Flows → Import**. The flow has the variable
`openplannerGroup`, a branch for kind `probe` that ends the check item, a Screen Pop with the meeting link, and a branch with a queue for each routing value of the
groups that share this business address. After importing, check the queues, validate, publish and
link the flow to the entry point; then press **Check integration**. The diagram and the details are
in `docs/wxcc-sample-flow.md`.

<!-- field: trial_call_enabled -->
### Trial call for the participant

With **Yes** the participant can try, before the appointment and in a real Webex meeting, whether
camera, microphone, speakers and browser work. It is not a separate test on our page: it is the
same Webex site and the same Webex environment as the call itself, so a problem shows up now.
The participant is alone in it; nobody from the team joins and no task is created. The link is in
the confirmation, the reminder, on the appointment page and on the page for the call. It only
works with a **Webex host**. The trial call uses the host's Webex site, which must allow
participants to enter before the host. **Check the integration** shows whether it does.

<!-- field: trial_call_minutes -->
### Trial call stays open (minutes)

How long one trial call stays open, from 10 to 60 minutes. Default 30. After that the Webex
meeting is deleted; the participant can open a new trial call.

<!-- field: trial_calls_per_booking -->
### Trial calls per appointment

How often one appointment may open a trial call, from 1 to 5. Default 3. Each trial call is a
meeting in Webex; this number stops one appointment from creating them without end.

<!-- field: join_client -->
### Webex page for the participant

Decides which Webex page the participant gets when they go from our page to the call, for the
appointment and for the trial call. **Straight into the browser** (the default) opens the Webex
web app at once: on a computer the participant has nothing to install and does not see the "app or
browser" question. **Webex lets the participant choose** gives Webex's own page, with the choice
between the app and the browser; pick it if your participants use the Webex app or your organisation
prefers it. On a phone or tablet Webex asks for the Webex app in both cases: there is no web
version there. The texts in the confirmation, on the call page and on the trial call page follow
this choice. Webex can change this behaviour itself; if the participant does see the choice between
app and browser anyway, set it to **Webex lets the participant choose**.

<!-- field: offer -->
### Offer per block

For every staffing block you choose what participants can do in it: **Appointments** (the block's
slots, the default), **Direct call** (start a video call right away, without an appointment;
the block then offers no slots) or **Appointments and direct call**. A staffing exception for
one day has the same choice.

During a block with agents that offers direct calls, the booking page shows the button
**Start a call now**. Outside such blocks, on holidays or during an emergency closure, the
button disappears by itself. The group itself no longer has an on/off switch: the offer sits
on the blocks, so a team can take direct calls in the morning and appointments in the
afternoon.

<!-- field: not_picked_up_minutes -->
### Not picked up after

How long a call may wait without an agent picking it up, from 1 to 120 minutes. After that it
is flagged in the overviews. A direct call that has waited this long also no longer counts
towards the maximum below.

<!-- field: direct_call_max_concurrent -->
### Maximum number of direct calls at once

How many direct calls may be open at once: waiting, or in a call with an agent. Usually that is
the number of agents who take direct calls. When the maximum is reached, the participant sees that
all agents are busy, with the choice to try again later or to book an appointment.

Empty means no limit: Webex Contact Center then distributes every call. Scheduled appointments
do not count; the slots already limit those.

<!-- field: embedding_enabled -->
### Embed on websites

With **Yes**, the group can also appear as a widget on the organisation's website, next to the
group's own page. This works only on the websites the organisation allows under
[Embedding on websites](embedding.md). With **No** (the default), the group can only be booked
on its own page, even when the widget is on an allowed website.

## Identity provider mapping

The **IdP value** is the name the identity provider uses for this group in a right, for
example `openplanner:group_admin:org-a/balie`. A new group starts with the code of its
web address as value. Only an organisation admin or the superuser changes the value; the form
warns how many staff members lose access until the identity provider sends the new value. The
full list is on **Identity provider mapping**.

<!-- field: idp_key -->
### IdP value

The name the identity provider uses for this group in a right, for example `balie` in
`openplanner:group_admin:org-a/balie`. Lower-case letters, digits and hyphens; unique
within the organisation. A new group starts with the code of its web address. When you change
the value, staff with a right on the old value lose access until the identity provider sends
the new one; the form counts how many that are.

## Archiving a group

At the bottom of the settings sits **Archive group…**, only for those who may archive groups
(organisation admin or superuser; the right `group.delete` is separate from changing settings).
The confirmation page says what happens and asks you to type the group's name; without a recent
login the portal first asks you to log in again.

After archiving:

- the page `/g/<web address>` and the widget answer "not found" at once; new appointments are
  no longer possible;
- the group leaves the overview, the lists and the menus. Under **Groups** the filter *Archived
  groups only* still shows it, read-only: every change is refused;
- the web address stays reserved, so a new group cannot take it;
- past appointments stay until the usual clean-up period; after that the portal erases the
  group by itself. The log keeps the web address.

Archiving is not possible while appointments lie ahead. Close the group first (**Closure**), let
participants move or cancel, or cancel the appointments from the closure screen, and archive
afterwards. Archiving cannot be undone in the portal.
