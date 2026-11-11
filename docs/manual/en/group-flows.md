# Flows of a service

A flow determines how a request to a service is handled: how it comes in, how service and
participant are in contact, which capacity the time comes from and which skills the operator
needs. You choose the name of a flow yourself; it determines nothing. Only what you set on this
screen determines how the flow works.

You open the screen via **service → Settings → Flows**. An organisation admin or the superuser can
read and change it. Before you change anything, the portal may ask you to log in again.

## The list

The list shows for each flow the name in your language (the other language underneath), the code,
how the request comes in, the contact channel, the capacity pool, the required skills and the
state: *Not active yet*, *Active* or *Archived*. If a flow has a draft in progress next to its
published revision, that is shown as a second label (*Draft in progress*). You see archived flows
with the tick **Archived flows only**. A service has at most the number of flows the superuser
sets (*Flows per service*, 20 by default); archived flows and the existing way of working do not
count.

A service that existed before flows did has its **existing way of working** as a flow. That flow
is in the list, follows the settings of the service itself and cannot be changed, archived or deleted here.

## Draft and revision

A flow has a **state**: *Not active yet* (a new flow, even when you have published it), *Active*
or *Archived*. What you edit is a **revision**: a published revision does not change any more, and
a **draft** is the revision you are still editing. The word draft is about the revision, never
about the state of the flow.

## Making and changing a flow

**New flow** makes a draft. A draft may be incomplete: the checklist next to the form shows what is
still missing. The form has four parts and one button, **Save draft**. A real mistake, such as a
pool that no longer exists or a default for an entry path the flow does not offer, is shown at the
field itself. An option that does not exist yet is in the list with *(to follow)* after its name
and cannot be chosen.

### Name

<!-- field: flow_code -->
#### Code

The address of the flow: lower-case letters, digits and hyphens, at most 40 characters, for
example `personal-conversation`. The code is not yet used by another flow of the service and cannot
be changed after the flow is made. The code `new` is reserved.

<!-- field: label_nl -->
#### Name in Dutch

The name participants read when the page is in Dutch, at most 80 characters. The name does not
determine how the flow works.

<!-- field: label_en -->
#### Name in English

The same name in English. Both names are required.

### Intake and contact

<!-- field: intake -->
#### How does a request come in?

You choose *By appointment*, *On demand, without an appointment* or both. Only the entry paths you
switch on here can be made the default further down. Save the draft after a change.

<!-- field: interaction_kind -->
#### Contact channel

Video call, telephone, in person or no conversation. Video call is in the list with *(to follow)*:
you cannot choose it yet. Below the contact channel you read when a request is ready for handling
and what the operator does first. That follows the **saved** contact channel, because the screen
works without script: save the draft after changing the channel.

<!-- field: handling_action -->
#### Choose the first action

Only for a channel with more than one possible first action (in person) do you choose the action;
an action that does not exist yet is there with *(to follow)*. If a submitted action does not fit
the chosen contact channel, the screen takes the first action of that channel and says so: *The
first action was adjusted to the chosen contact channel.*

<!-- field: duration_minutes -->
#### Expected duration

The length of one appointment in minutes, in steps of 5. The limits are shown at the field.

### Capacity and skills

<!-- field: capacity_pool_id -->
#### Capacity pool

A new flow draws on a **capacity pool** of the organisation, not on the service's own staffing.
The organisation admin manages pools under [Skills and capacity](organisation-skills-capacity.md).
An archived pool the flow already uses stays (with *(archived)*); you cannot choose another
archived pool.

<!-- field: required_skills -->
#### Required skills

You tick the skills every operator needs. The pool must guarantee them; otherwise the point
*Skills* on the checklist says **Missing**.

<!-- field: subjects -->
#### Subjects in this flow

For each subject of the service you tick whether the flow offers it. The table also shows the extra
skills the subject asks for now (*None* when there are none). You choose those on a page of their
own: after saving the draft, an offered subject has **Choose extra skills** next to it. There you
tick the extra skills and save them in the draft; you return to the subjects. A subject you no
longer offer loses the extra skills you chose for it; a subject that stays offered keeps them when
you save the form, even if you changed them on their own page in the meantime. A subject the
service itself no longer has is not in the table any more and disappears from the draft when you
save it.

### Handling

<!-- field: dispatch -->
#### Where the request goes

For now that can only be the own queue; the contact center is in the list with *(to follow)*.

<!-- field: completion -->
#### Completion

For now that can only be by the operator; completion by an event from the provider is in the list
with *(to follow)*.

<!-- field: collects_email -->
#### Which data do you ask for?

Whether the form also asks for an email address (needed for a confirmation by email). The name is
always asked.

<!-- field: defaults -->
#### Default flow

You can make the flow the default for appointments and for on demand. A service has at most one
default flow per entry path. Once a flow has a published revision, its default paths are fixed.

## The checklist

Next to the form are six points: name and intake, capacity, skills, contact channel and dispatch,
completion, and retention and deletion. Each point has a state in words: **In order**, **Missing**
or **To follow** (that part of OpenPlanner comes in a later version; the sentence says which
part). The heading says what the checklist judges and whether that is in order: *The draft is
complete* or *The draft is not complete yet* when there is a draft, otherwise *The published
revision is ready to use* or *… is not ready to use yet*.

## Publishing, activating, discarding and archiving

- **Publish draft** makes the saved draft the published revision. A published revision does not
  change any more; existing appointments keep the revision they were made under. Publishing does
  not activate the flow. The draft of a flow that is already active is judged first: a draft that
  is not complete is not published, so an incomplete revision never comes into use.
- **Prepare a new revision** is on a flow without a draft and makes a draft as a copy of the
  published revision.
- **Activate flow** is judged on the published revision and is possible only when every point of
  the checklist is **In order**. There is no button that is switched off: when it cannot be done,
  a sentence says why. *Publish the draft first.* when there is no published revision yet. *New
  flows cannot be activated in this version yet* as long as closing and deleting data
  automatically for new flows does not exist: you can prepare and publish the flow. Otherwise the
  sentence says activation is possible once the published revision is in order on every point.
- **Discard the draft** asks for confirmation first and throws away only the draft of a flow that
  already has a published revision; that revision stays. A flow that was never published has
  nothing to fall back on: you delete that one. You cannot discard a draft of an archived flow.
- **Archive the flow** asks for confirmation first and works on a flow that is active. No new
  requests come in through the flow then; existing requests keep it. A draft the flow still has is
  discarded with it. An archived flow cannot be activated again in the portal.
- **Delete the flow** asks for confirmation first and works on a flow that has never been active,
  even if it already has a published revision. The flow and all its revisions go; this cannot be
  undone. While a booking refers to the flow it cannot be deleted. A flow that is or was active
  you archive. A flow that took up room under *Flows per service* gives that room back.

Every change goes into the [audit log](audit.md), with the code of the flow and the number of the
revision, never the name.
