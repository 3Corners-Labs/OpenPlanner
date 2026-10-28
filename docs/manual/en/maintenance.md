# Maintenance mode

Maintenance mode puts the whole installation on read only while you work on the platform: an
update, a restore, an integration that fails, or an incident you want to investigate without
the data changing underneath. Open the screen with **Maintenance** in the menu. Only the
superuser sees this screen.

Maintenance mode applies to every organisation at once. To stop one team or one organisation
from taking appointments for a while, use an emergency closure instead.

## What happens while maintenance mode is on

- **Citizens** cannot book, reschedule or cancel an appointment, nor start a direct call. They
  see a notice that maintenance is in progress, with the expected end if you entered one and
  the team's alternative contact options (or the organisation's), if there are any. Viewing an appointment and joining a call that is already confirmed still work.
- **Confirmed appointments** go ahead: reminders, work items and video calls keep working.
  Personal data is removed on time as always.
- **Admins** can read everything but change nothing. Every page shows the **Maintenance mode**
  bar at the top. A refused change is recorded in the audit log with the reason **Refused:
  maintenance mode**.
- **The superuser** can only change integrations (email, SMS, Webex), updates and this switch,
  and use **Check integration** on a group. Everything else is read only for the superuser
  too.

## Switching maintenance mode on

<!-- field: reason -->
### Reason

Why you switch maintenance mode on: an update, a restore of an earlier version, repairing an
integration, investigating an incident or other maintenance. The reason is shown on this
screen and in the audit log.

<!-- field: expected_end -->
### Expected end

When you expect to be done, in the admin portal's time zone. Citizens see this time on the
maintenance page. It is information only: maintenance mode does not end by itself. Leave both
fields empty if you do not know.

When maintenance mode is already on, the same form changes the reason and the expected end.
The time it was switched on stays the same.

## Switching maintenance mode off

Choose **Switch maintenance mode off** as soon as you are done. Citizens and admins can then
do everything they are used to again.

When maintenance mode is on **by the server**, the server operator switched it on with the
setting `MAINTENANCE_MODE`, usually because the database itself is being worked on. You
cannot switch that off here; ask the server operator.

## Updates

An update or restore from the **Updates** screen switches maintenance mode on before the
backup is taken, and off again once the update succeeded or was rolled back. When an update
fails completely, maintenance mode stays on, so citizens do not see a half-working system.
Switch it off only once everything works again.

If you had switched maintenance mode on yourself before the update started, it is off after an
update that succeeded or was rolled back. Switch it on again if you are not done yet.

When maintenance mode has been on for more than four hours (the default setting), the system
event **Maintenance mode on for long** is added to the audit log, and again every day while it
stays on.
