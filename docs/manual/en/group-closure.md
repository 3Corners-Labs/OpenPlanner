# Emergency closure of a team

An emergency closure puts a team's appointments on hold, for instance during an outage, a
strike or when the building is closed. Open the screen through **team → Emergency closure**. A group
admin, organisation admin or superuser can close a team. To close every team of the
organisation at once, use the organisation's closure screen.

## Closing

Under **Close the team in an emergency**, enter from when until when the team is closed, and a notice for
citizens in Dutch and English. A heading is optional; without one the citizen sees
"Temporarily closed". Leave the end empty to keep the team closed until you lift the closure.

As soon as the closure starts:

- citizens see the closure page instead of the calendar on the team's page: the heading, the
  notice, the image and the alternative contact options, in that order;
- confirmed appointments inside the closure are **put on hold**, not cancelled. The citizen
  gets an email with the notice and can choose a new time or cancel.

A closure that starts later is shown to citizens beforehand as a notice above the calendar.

### Starting from a scenario

If you prepared a scenario, choose it under **Start from a scenario** and click **Use**: the
form is filled with the scenario's notice and heading. On closing, the closure also takes over
the scenario's contact options and image. See
[Scenarios for an emergency closure](closure-scenarios.md).

## Image

A running or planned closure can have one image, such as a pictogram for "closed" or
"outage". Citizens see it on the closure page, below the notice. Never put information in the
image only: whoever cannot see it reads just the text.

<!-- field: image -->
### Image file

A PNG, JPEG or WebP file of at most 500 kB. We recognise the kind of file from its content,
not from its name; SVG and other files are refused. Data a photo can carry, such as where it
was taken or the name of its author, is removed before the file is stored. To change only the
description, leave this field empty.

<!-- field: alt_nl -->
### Description (Dutch)

What the image shows, in one sentence of at most 250 characters, for citizens who use a screen
reader. For example: "Pictogram van een gesloten deur".

<!-- field: alt_en -->
### Description (English)

The same description in English. Both languages are required, unless you tick that the image
is **decoration only**: a screen reader then skips it.

### Who sees the image

The image is public while the closure is running or planned, and is served from our own
website, without other parties. After the closure ends it is no longer shown. **Remove image**
takes it away at once. The image is not included in emails.

## Updating and lifting

Under **Change the emergency closure** you change the end, the notice and the heading. Tick whether
citizens with an appointment on hold get the new notice by email. When a later end brings more
appointments inside the closure, those are put on hold too.

**Lift the emergency closure** opens the team again at once. Tick whether appointments on hold that can
still go ahead are confirmed again; the citizen then gets an email.

## Appointments on hold

The list shows each appointment's reference and original time, never the citizen's details.
Per appointment you can move it to a free time, confirm it again or cancel it, or all at once.
The personal data of an appointment that stays on hold is removed 30 days after the closure
ends.

## What is recorded

Closing, updating, lifting and saving or removing the image go to the team's audit log, with
counts. The notices, descriptions and the image itself are not in the log.
