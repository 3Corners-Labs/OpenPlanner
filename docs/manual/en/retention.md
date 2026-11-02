# Retention

This screen shows, per group, whether participants' personal data is removed on time. You open it
with **Retention** in the menu. Organisation admins, the Compliance and privacy role and the
superuser can view it, each for their own organisations.

The screen shows counts and times only. You never see names, email addresses or phone
numbers here, nor which booking it is.

## When data is removed

- **Appointment over**: 24 hours after the end of the appointment.
- **Appointment cancelled**: 24 hours after the cancellation.
- **Appointment suspended by an emergency closure**: 30 days after the closure ends, and at
  the latest the organisation's maximum after the original appointment (90 days by default),
  also while the closure has no end.

Removal deletes the personal fields from the booking, the links to its meeting and work item
at the providers, and the reason texts of its timeline. The booking itself stays with its
reference and times; together with data held elsewhere these could still point to a person.

The booking itself goes later too: the platform administrator sets how many days after the details
were deleted that happens (730 by default). What stays is a count per group, day and final status,
with no personal data and no reference. The figures on this screen do not change because of it, and
**Last removed** stays correct.

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
