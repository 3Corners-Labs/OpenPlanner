# Retention

This screen shows, per group, whether citizens' personal data is removed on time. You open it
with **Retention** in the menu. Organisation admins, the Compliance and privacy role and the
superuser can view it, each for their own organisations.

The screen shows counts and times only. You never see names, email addresses or phone
numbers here, nor which booking it is.

## When data is removed

- **Appointment over**: 24 hours after the end of the appointment.
- **Appointment cancelled**: 24 hours after the cancellation.
- **Appointment suspended by an emergency closure**: at most 30 days after the closure ends.
  While the closure has no end, the booking does not count yet.

After removal the booking stays, without personal data, for statistics.

## What the columns mean

- **With personal data**: bookings whose data is still there. That is normal for bookings
  that are still to come or have just ended.
- **Past the retention period**: bookings whose data is still there an hour after the moment
  it should have been removed. This should be 0.
- **Failed removals**: removals that failed after every attempt. This should be 0.
- **Last removed**: when the data of a booking in the group was last removed.
- **Status**: **In order** or **Action needed**. The status is always written out.

## When action is needed

Report it to the operator of the installation. The audit log shows the error code at the
system event **Remove personal data**. A backlog usually means the background processing
(the worker) is not running.
