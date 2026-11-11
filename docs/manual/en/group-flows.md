# Flows of a service

A flow determines how a request to a service is handled: how the participant signs up, how service
and participant are in contact, which capacity the time comes from and which skills the operator
needs. You choose the name of a flow yourself; it determines nothing. Only what you set on this
screen determines how the flow works.

You open the screen via **service → Settings → Flows**. An organisation admin or the superuser can
read and change it. Before you change anything, the portal may ask you to log in again.

## The list

The list shows for each flow the name in your language (the other language underneath), the code,
how the participant signs up, the contact channel, the capacity pool, the required skills and the
state: draft, active or archived. If a flow has a draft in progress next to its published revision,
that is shown as a second label. You see archived flows with the tick **Archived flows only**. A
service has at most the number of flows the superuser sets (*Flows per service*, 20 by default).

A service that existed before flows did has its **existing way of working** as a flow. That flow
is in the list, follows the settings of the service itself and cannot be changed or archived here.

## Making and changing a flow

**New flow** makes a draft. A draft may be incomplete: the checklist next to the form shows what is
still missing. The form has four parts and one button, **Save draft**. A real mistake, such as a
pool that no longer exists or a default for an entry path the flow does not offer, is shown at the
field itself.

<!-- field: flow_code -->
### Code

The address of the flow: lower-case letters, digits and hyphens, at most 40 characters, for example
`personal-meeting`. The code is not yet used by another flow of the service and cannot be changed
after you make the flow. The code `new` is reserved.

<!-- field: label_nl -->
### Name in Dutch

The name participants read when the page is in Dutch, at most 80 characters. The name does not
determine how the flow works.

<!-- field: label_en -->
### Name in English

The same name in English. Both names are required.

### Intake and contact

You choose how the participant signs up (by appointment, on demand or both) and the contact
channel: video call, telephone, in person or no conversation. Below the contact channel you read
when a request is ready for handling and what the operator does first. That follows from the
chosen channel and is fixed; you do not choose it yourself. Only for a channel with more than one
possible first action (in person) do you choose that action, as far as it exists. Because the
screen works without script, these details follow the **saved** contact channel: save the draft
after a change.

<!-- field: duration_minutes -->
### Expected duration

The length of one appointment in minutes, in steps of 5. The bounds are shown at the field.

### Capacity and skills

A new flow draws on a **capacity pool** of the organisation, not on the service's own staffing.
The organisation admin manages pools under [Skills and capacity](organisation-skills-capacity.md).
You tick the skills every operator needs, and for each subject of the service you choose whether
the flow offers it and which extra skills that subject needs. The pool must guarantee every skill
the flow and its subjects require; otherwise the point *Skills* on the checklist is **Missing**.

### Handling

You choose where requests go and what completes a request. Today that can only be the own queue
and completion by the operator; the contact center and completion by an event from the provider
are in the list, marked *to follow*. You say whether the form also asks for an email address
(needed for a confirmation by email); the name is always asked. Finally you can make the flow the
**default flow** for appointments and for on demand. A service has at most one active flow per
entry path for that. Once a flow has a published revision, its default paths are fixed.

## The checklist

Next to the form are six points: name and intake, capacity, skills, contact channel and dispatch,
completion, and retention and deletion. Each point has a state in words: **In order**, **Missing**
or **To follow** (that part of OpenPlanner comes in a later version). The heading says whether the
flow is ready to use. The checklist judges the saved draft; when there is no draft, the published
revision.

## Publishing, activating, discarding and archiving

- **Publish draft** makes the saved draft the published revision. A published revision does not
  change any more; existing appointments keep the revision they were made under. Publishing does
  not activate the flow.
- **Prepare a new revision** is on a flow without a draft and makes a draft as a copy of the
  published revision.
- **Activate flow** is judged on the published revision and is possible only when every point of
  the checklist is **In order**. As long as the retention and deletion of requests without an
  appointment does not exist yet, no new flow can be activated; the button is then off, with the
  explanation.
- **Discard the draft** asks for confirmation first. A flow that was never published disappears
  from the list; otherwise the published revision stays.
- **Archive the flow** asks for confirmation first, and works on an active flow only. No new
  requests come in through the flow then; existing requests keep it.

Every change goes to the [audit log](audit.md), with the code of the flow and the number of the
revision, never with the name.
