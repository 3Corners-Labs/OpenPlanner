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
| View audit log (`audit.view`) | Organisation | yes | yes | – |
| Manage integrations and secrets (`integrations.manage`) | Organisation | yes | – | – |
| Manage an organisation's branding (`branding.manage`) | Organisation | yes | – | – |
| Choose holiday calendars for an organisation (`holidays.assign`) | Organisation | yes | yes | – |
| Create groups (`group.create`) | Organisation | yes | yes | – |
| View group (`group.view`) | Group | yes | yes | yes |
| Change group settings (`group.edit`) | Group | yes | yes | – |
| Manage opening hours and exceptions (`schedule.manage`) | Group | yes | yes | yes |
| Manage texts (`texts.manage`) | Group | yes | yes | yes |
| Manage reminders (`reminders.manage`) | Group | yes | yes | – |
| View bookings (`bookings.view`) | Group | yes | yes | yes |

## Citizens

Citizens never log in. Their rights depend on the management link from their
confirmation email and on the change deadline (the group's dispatch lead).

| Action | Condition |
|---|---|
| View available times (`citizen.availability_view`) | Anyone |
| Book an appointment (`citizen.book`) | Anyone |
| View own appointment (`citizen.booking_view`) | With the management link from the confirmation |
| Move own appointment (`citizen.booking_change`) | With the management link, until the task goes to the queue |
| Cancel own appointment (`citizen.booking_cancel`) | With the management link, until the task goes to the queue |
