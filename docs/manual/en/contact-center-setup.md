# Setting up the contact center

This topic is for the organisation's contact-center administrator. It sets up the connection
between OpenPlanner and Webex Contact Center (WxCC) step by step, one screen per step: shortly
before an appointment starts, a *work item* with the link to the video call then reaches the
team's queue, and the first free agent picks it up.

Who does what:

- **The Webex and contact-center administrator** does steps 1 to 6 in the Webex developer
  portal and in Control Hub.
- **The organisation admin** (or the group admin) does steps 7, 9 and 10 in the OpenPlanner
  portal, on the group's **Settings** tab.
- **The superuser or OpenPlanner's host team** puts the Service App's tokens on the server and
  registers the webhooks (steps 1 and 8), and pastes the bot's token (step 11).

Control Hub is in English; the names of buttons and screens are given as they appear there. The
technical detail for the host team is in the runbook, under *Webex meetings and Webex Contact
Center*.

## Step 1. The Webex Service App

Screen: **developer.webex.com → My Webex Apps → Create a New App → Service App**, then
**Control Hub → Apps → Service Apps**.

Create a Service App with exactly these scopes. Keep the *client secret* when it is shown.

| Scope | What for | Without it |
|---|---|---|
| `meeting:admin_schedule_write` | create and delete video calls on behalf of the group's Webex host | no video calls |
| `meeting:admin_schedule_read` | read meetings, events about meetings | no live meeting status |
| `meeting:admin_participants_read` | events about who joins the meeting | no participant counts |
| `cjp:task_write` | create work items and mark them cancelled | no work items |
| `cjp:task_read` | task access, goes with `cjp:task_write` | – |
| `cjp:user` (optional) | end a cancelled work item | a cancelled item stays open until an agent closes it |
| `cjp:config_read` | read the configuration for **Check integration**, read a task's state | the check and the task reconcile do not work |
| `cjp:config_write` | create the subscription to task events (step 8) | OpenPlanner does not hear whether an agent picks the item up |

A Control Hub administrator authorises the app under **Apps → Service Apps**. That
administrator needs the *Contact Center administrator* role, and for the meeting events also
*site admin* of the Webex site or *Compliance Officer*. Cisco must have enabled Work Items
(beta) for the organisation.

Then generate the tokens in the developer portal. The client id, the client secret and the
*refresh token* go to the host team, who put them on the server (secrets are never entered in
the portal). Adding a scope later means: authorise again and generate new tokens.

Check the app with **Check the Service App** on **Integrations** (superuser only, see
[Integrations](integrations.md)). Every line should be **OK**; on a 401 or 403 the explanation
names the scope that is probably missing.

## Step 2. The work item channel

Screen: **Control Hub → Contact Center → Digital settings → Custom Channels**.

Create a channel with *Channel type* **Work item**, for example `openplanner-video`. You enter
this name for the group as **WxCC work item channel**, spelled exactly the same, case included.

## Step 3. The asset and the data schema

Screen: **Control Hub → Contact Center → Digital settings → Assets**.

Create an asset on that channel with its own **Business address**, for example
`appointments@voorbeeld.nl`. That address becomes the group's **WxCC business address**. Give
the asset exactly these fields under **Data schema**:

| Field key | Display name (free) | Viewable | Required |
|---|---|---|---|
| `bookingReference` | Appointment | Yes | **Yes** |
| `firstName` | First name | Yes | No |
| `startTime` | Start time | Yes | No |
| `groupName` | Team | Yes | No |
| `meetingLink` | Video call | Yes | No |
| `kind` | Kind | Yes | No |

Only `bookingReference` is required: a cancellation sends nothing else. Any other required
field makes WxCC refuse OpenPlanner's work items.

One asset for all groups is the simplest: every group gets the same channel and business
address, and only the routing value (steps 5 and 7) differs.

## Step 4. The entry point

Screen: **Control Hub → Contact Center → Customer experience → Channels**.

Create a channel of type **Work item** that points at the asset of step 3, set *Status* to
active and choose the *Routing flow* of step 5.

## Step 5. The flow

Screen: **Control Hub → Contact Center → Customer experience → Flows**, then **Flow Designer**.

The flow has three parts OpenPlanner needs:

1. **Routing to the right queue.** A flow variable `openplannerGroup` (type String). OpenPlanner
   fills it with the group's **Routing value for the WxCC flow** (empty: the group's web
   address). Put a **Case** on it with one branch per routing value to a **Queue Contact** with
   that group's queue, and a default branch to a fallback queue.
2. **A branch for the probe.** Before the queue, put a **Case** on `kind` with a branch `probe`
   that goes straight to **End Flow** (or *Disconnect Contact*). **Check integration** sends
   a probe work item with `kind` = `probe` and waits until it has been created and ended. The
   value `probe` is fixed; the flow and OpenPlanner must agree on it.
3. **Screen Pop.** A **Screen Pop** with `meetingLink` as its URL, so the agent opens the video
   call in one click.

Publish the flow after every change. Instead of building it by hand, the host team can generate
a sample flow to import (see *Sample flow to import* in [Group settings](group-settings.md)).

## Step 6. The queues

Screen: **Control Hub → Contact Center → Customer experience → Queues**, and for the agents
**User management → Multimedia profiles**.

One queue per team with *Channel type* **Work item**, with the team of agents assigned and a
RONA timeout. The agents' *multimedia profile* must allow work items. Name the queue so that it
contains the group's routing value (for example `ACME_video_permits` for routing value
`permits`): the check then shows per group which queue it uses.

## Step 7. The group's fields in OpenPlanner

Screen: **Groups → group → Settings**, part **Integrations: video and contact centre**.

| Field | What to enter |
|---|---|
| **How appointments reach agents** | **Contact center** |
| **Send to the queue (minutes before)** | how long before the start the work item comes, 5 by default |
| **Webex host (email address)** | a functional licensed Webex account, for example `videocalls@voorbeeld.nl` |
| **WxCC work item channel** | the name from step 2 |
| **WxCC business address** | the address from step 3 |
| **Routing value for the WxCC flow** | the value of the Case branch from step 5; empty is the group's web address |

Save. The explanation of each field is behind the (i) next to the field and in
[Group settings](group-settings.md).

## Step 8. Registering the webhooks

Screen: none; the host team does this on the server with `register-webhooks` (runbook,
section 4). The superuser sees the result on **Integrations**, under **Registrations at Webex
and WxCC**.

Through the webhooks OpenPlanner hears that a meeting starts and ends, and that a work item came
in, was picked up or was ended. The host team registers again after a group got another Webex
host, after OpenPlanner's address changed, and after an outage in which WxCC set the
subscription to *inactive*. All registrations should be **Current**, and nothing should be
listed under **Missing**.

## Step 9. Check integration

Screen: **Groups → group → Settings**, card **Check integration**, button **Check now**.

For this one group the check does what a real appointment does: a probe meeting is scheduled
and deleted at once, the Control Hub configuration is read, and a probe work item goes through
the flow, with the first name "Probe" and no participant data. Each line says **OK**,
**Warning**, **Error** or **Skipped**, with what to change. Save changes first: the check uses
the saved settings. The latest result stays on the page; after that the platform checks the
group by itself, once a day by default.

| Part | What its lines say | On an error |
|---|---|---|
| **Integrations** | whether there is a Service App | step 1 |
| **Webex (video call)** | whether the host exists and is licensed, whether the probe meeting and the trial call work | Webex's answer is on the line; check the host and the site settings |
| **Webex Contact Center (routing to the queue)** | region, channel, asset, data schema, entry point, flow (`openplannerGroup`, a branch for the routing value, the `probe` branch), queues, queue naming, Screen Pop, and the probe work item | steps 2 to 6; the link on the line opens the explanation |
| **Webhooks (events back to OpenPlanner)** | whether the webhook address is https, whether the webhooks and the subscription are active on the current address, and whether a signed test message arrives | step 8 |
| **Background jobs (worker)** | whether the worker runs, which version, whether it makes video calls and work items, and whether jobs of the group failed or are late | tell the host team |

The lines about the probe work item:

- **created and ended**: token, scopes, asset, flow, subscription and webhook work in one go.
  This is the goal.
- **no branch for kind 'probe'** (warning): OpenPlanner ends the probe itself. Add the branch of
  step 5.
- **no task:new in time**: WxCC reports nothing back. See **Webhooks** in the same check for the
  subscription and the test message (step 8).
- **arrived, but the flow did not end it**: the `probe` branch does not work; check the Case on
  `kind`.
- **could not be ended**: end it in Agent Desktop, or add the scope `cjp:user`.
- **refused** or **reported as failed**: the data schema, the entry point or the queue refuses
  it; read the reason on the line.

A failed probe counts towards the connection's health: the organisation's contact center is
then **Down** (see [Integrations](integrations.md)).

## Step 10. Fallback per group

Screen: **Groups → group → Settings**, fields **Fallback when the contact center fails** and
**The team's Webex space (fallback)**.

With **Own queue** an appointment goes to OpenPlanner's queue, labelled **Fallback**, when its
dispatch fails for good after the retries, or while the organisation's contact center is
**Down**. In the latter case the whole group falls back. Choose this only if the team knows the
queue and can work it: agents then sign in to OpenPlanner and pick up appointments on the
**Queue** tab (see [Queue](queue.md)).

What the team sees:

- an alert to the administrators (step 11) that the group falls back;
- at the top of the **Queue** the banner **Fallback active**, with since when;
- on every appointment in the queue and in the agenda the label **Fallback**;
- with **The team's Webex space**: the organisation's bot posts a message there that the group
  falls back, with a link to the queue, and for each appointment that lands in the queue one
  line with the start time, the first name and the same link. Without a space the alerts go only
  to the addresses of step 11.

Once the contact center has been fine for a while (setting *Recovered after this many minutes
without failures*, ten minutes by default), the fallback ends by itself and appointments not
yet picked up go to the contact center after all. Whoever may change the group's appointments
can also end the fallback with **End the fallback** on the **Queue**. Both are in the audit log.
The participant notices nothing: the link, the lobby and the rules for cancelling and moving
stay the same.

### Privacy during a fallback

During a fallback the **first name and the start time** of an appointment are posted in the
team's Webex space. Those messages stay there, also after OpenPlanner has deleted the
appointment's data: OpenPlanner cannot delete messages in Webex. They fall under the
organisation's own retention rules for Webex. The link to the call is never posted in the space:
only whoever picks the appointment up in the queue gets it. So choose a space with only the
team's agents in it. The organisation chooses this by switching the fallback on with a space.

## Step 11. The bot for alerts

Screen: **developer.webex.com → My Webex Apps → Create a New App → Bot**, then in OpenPlanner
**Organisations → organisation → Participants → Channels**, card **Alerts to administrators**
(superuser only).

Alerts about the connection (degraded or down, tasks that were not reported back, a failed
probe, failed jobs, the fallback) go to an administrators' email address or to a Webex space.
For a Webex space:

1. Create a **bot** in the Webex developer portal, with only the scope `spark:messages_write`.
   Do not use the Service App of step 1: the bot may only send messages, never create meetings
   or tasks.
2. Add the bot to the administrators' Webex space (and to the space of every team with a
   fallback, step 10).
3. Paste the bot's token under **Webex bot** in the field **Bot token** and choose **Save**. The
   token is stored encrypted and never shown again.
4. Under **Add an address**, choose **Kind** **Webex space**, enter the space's id under
   **Address or room id**, choose under **From** which alerts, and choose **Add**.
5. Choose **Send a test alert** next to the address. The portal says *The test alert was sent.*
   and the message is in the space; if not, check the bot's token and the room id.

An alert holds only codes, the organisation, the group, the time and a link into the portal,
never participants' data. More on repeating and counting is in [Channels](channels.md).
