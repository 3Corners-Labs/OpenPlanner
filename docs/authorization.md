# Authorisation matrix

Generated from `app/domain/authorization.py` by `python -m app.cli authz-matrix`.
Do not edit by hand: CI fails when this file differs from the code (ADR 0014).

## Admins

Roles come from the identity provider (ADR 0013). A role grants a permission only
within its scope: superuser everywhere, organisation admin in the organisations of
their claim and those organisations' groups, group admin in the groups of their claim.

| Permission | Level | Superuser | Organisation admin | Group admin |
|---|---|---|---|---|
| View overview (`dashboard.view`) | Portal | yes | yes | yes |
| View own rights (`rights.view`) | Portal | yes | yes | yes |
| View rights matrix (`matrix.view`) | Portal | yes | yes | yes |
| Manage organisations (`organisation.manage`) | Portal | yes | – | – |
| Maintain holiday calendars (for every organisation) (`holidays.manage`) | Portal | yes | – | – |
| Manage API clients (`api_clients.manage`) | Portal | yes | – | – |
| Update the installation (`system.update`) | Portal | yes | – | – |
| View audit log (`audit.view`) | Organisation | yes | yes | – |
| Manage integrations and secrets (`integrations.manage`) | Organisation | yes | – | – |
| Manage an organisation's branding (`branding.manage`) | Organisation | yes | – | – |
| Choose holiday calendars for an organisation (`holidays.assign`) | Organisation | yes | yes | – |
| Create groups (`group.create`) | Organisation | yes | yes | – |
| Close every team of an organisation at once (`organisation.closure`) | Organisation | yes | yes | – |
| View group (`group.view`) | Group | yes | yes | yes |
| Change group settings (`group.edit`) | Group | yes | yes | – |
| Manage staffing and staffing exceptions (`schedule.manage`) | Group | yes | yes | yes |
| Manage texts (`texts.manage`) | Group | yes | yes | yes |
| Manage reminders (`reminders.manage`) | Group | yes | yes | – |
| View bookings (`bookings.view`) | Group | yes | yes | yes |
| Close a team in an emergency and handle appointments on hold (`closure.manage`) | Group | yes | yes | yes |

## Participants

Participants never log in. Their rights depend on the management link from their
confirmation email and on the change deadline (the group's dispatch lead).

| Action | Condition |
|---|---|
| View available times (`participant.availability_view`) | Anyone |
| Book an appointment (`participant.book`) | Anyone |
| View own appointment (`participant.booking_view`) | With the management link from the confirmation |
| Move own appointment (`participant.booking_change`) | With the management link, until the task goes to the queue |
| Cancel own appointment (`participant.booking_cancel`) | With the management link, until the task goes to the queue |
| Move or cancel an appointment on hold (`participant.suspended_booking_change`) | with the management link, while the appointment is on hold |

## Machines (API clients)

Contact-center bots never get a role (ADR 0023). The superuser registers each one as
an API client of one organisation, optionally narrowed to some of its groups, with
the scopes below. There is no superuser equivalent for machines.

| Scope | Level |
|---|---|
| Read free times (`availability:read`) | Group |
