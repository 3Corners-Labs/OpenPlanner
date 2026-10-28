# Authorisation matrix

Generated from `app/domain/authorization.py` by `python -m app.cli authz-matrix`.
Do not edit by hand: CI fails when this file differs from the code (ADR 0014).

## Admins

Roles come from the identity provider (ADR 0013). A role grants a permission only
within its reach: superuser everywhere; organisation admin and auditor in the
organisations of their claim and those organisations' groups; group admin in the
groups of their claim. The auditor reads only and never sees personal data (ADR 0039).

Bounds (ADR 0039): *locked* permissions belong to the superuser only; *personal data*
permissions show citizens' data and are never held by the auditor.

| Permission | Level | Bounds | Superuser | Organisation admin | Group admin | Compliance and privacy (read-only) |
|---|---|---|---|---|---|---|
| View overview (`dashboard.view`) | Portal | – | yes | yes | yes | yes |
| View own rights (`rights.view`) | Portal | – | yes | yes | yes | yes |
| View rights matrix (`matrix.view`) | Portal | – | yes | yes | yes | yes |
| Manage organisations (`organisation.manage`) | Portal | locked | yes | – | – | – |
| Maintain holiday calendars (for every organisation) (`holidays.manage`) | Portal | locked | yes | – | – | – |
| Manage API clients (`api_clients.manage`) | Portal | locked | yes | – | – | – |
| Update the installation (`system.update`) | Portal | locked | yes | – | – | – |
| Adjust roles (`roles.manage`) | Portal | locked | yes | – | – | – |
| Manage integrations and secrets (`integrations.manage`) | Organisation | locked | yes | – | – | – |
| Manage an organisation's branding (`branding.manage`) | Organisation | – | yes | – | – | – |
| Choose holiday calendars for an organisation (`holidays.assign`) | Organisation | – | yes | yes | – | – |
| Create groups (`group.create`) | Organisation | – | yes | yes | – | – |
| Close every team of an organisation at once (`organisation.closure`) | Organisation | needs `closure.manage` | yes | yes | – | – |
| View retention (`retention.view`) | Organisation | – | yes | yes | – | yes |
| View audit log (`audit.view`) | Group | – | yes | yes | yes | yes |
| View group (`group.view`) | Group | – | yes | yes | yes | yes |
| Change group settings (`group.edit`) | Group | needs `group.view` | yes | yes | – | – |
| Manage staffing and staffing exceptions (`schedule.manage`) | Group | needs `group.view` | yes | yes | yes | – |
| Manage texts (`texts.manage`) | Group | needs `group.view` | yes | yes | yes | – |
| Manage reminders (`reminders.manage`) | Group | needs `group.view` | yes | yes | – | – |
| View bookings (`bookings.view`) | Group | personal data | yes | yes | yes | – |
| Close a team in an emergency and handle appointments on hold (`closure.manage`) | Group | needs `group.view` | yes | yes | yes | – |

## Custom roles

The superuser can make custom roles (ADR 0039): a copy of a template below, with a
name, a role claim value of its own and delegable permissions switched on or off. A
custom role keeps the reach of its template and never reaches everything. Every role
holds the permissions every admin holds and the permissions its permissions need. A
copy of the auditor only reads and never holds a personal-data permission. Custom
roles are data; `decide` clips each one to these bounds again on every request.
Held back from custom roles for now: `branding.manage` (ADR 0039 B3, until O5).

| Template | Reach | Delegable permissions |
|---|---|---|
| Organisation admin | organisations | `audit.view`, `bookings.view`, `closure.manage`, `dashboard.view`, `group.create`, `group.edit`, `group.view`, `holidays.assign`, `matrix.view`, `organisation.closure`, `reminders.manage`, `retention.view`, `rights.view`, `schedule.manage`, `texts.manage` |
| Group admin | groups | `audit.view`, `bookings.view`, `closure.manage`, `dashboard.view`, `group.edit`, `group.view`, `matrix.view`, `reminders.manage`, `rights.view`, `schedule.manage`, `texts.manage` |
| Compliance and privacy (read-only) | organisations | `audit.view`, `dashboard.view`, `group.view`, `matrix.view`, `retention.view`, `rights.view` |

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
| Move or cancel an appointment on hold (`citizen.suspended_booking_change`) | with the management link, while the appointment is on hold |

## Machines (API clients)

Contact-center bots never get a role (ADR 0023). The superuser registers each one as
an API client of one organisation, optionally narrowed to some of its groups, with
the scopes below. There is no superuser equivalent for machines.

| Scope | Level |
|---|---|
| Read free times (`availability:read`) | Group |
