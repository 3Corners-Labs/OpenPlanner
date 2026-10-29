# Advanced settings

This screen shows how the installation is configured and where each value comes from. Open it
with **Advanced settings** in the menu. The superuser and the Compliance and privacy role can
view it. The superuser can change some of the settings; the auditor only reads.

Secret values, such as passwords and keys, are never shown: only whether they are set.

## System status

The settings that only the server operator sets, in the server's environment or in a file.
They are needed before the portal or the database works, or they belong to the server itself.
For each one you see whether it is set and where it comes from, never its value.

When the installation does not run as production (for example a test environment), the
screen also lists what production would refuse: a missing key or an address without https.
Production never starts with any of these.

Support may ask for a screenshot of this screen instead of the server's configuration.

## Platform settings

Below System status, every setting of the platform is listed by topic, with its current value
and its source:

- **Default**: the value the software ships with.
- **Admin portal**: a value set in the portal.
- **Environment**: a value the server operator set. The setting is then **locked by the server
  operator**, and the screen names the variable. A value in the portal does not apply while
  the lock is there.
- **File**: a secret the server reads from a file.

A changed value applies within about ten seconds, without a restart.

## Changing a setting

The superuser changes the settings for operations, retention, storage, rate limits, admin
sessions, staff and the planner. The fallback mail server, Webex, updates and the login
mapping follow in a later version.

- Enter the new value under **Change** and choose **Save**. The portal only accepts values
  within its bounds, for example 1 to 30 bookings per hour per address. A value outside them
  is refused at the row, with the reason.
- Some settings depend on each other: critical storage must stay below low storage, and the
  reminder to rebook must come before the request lapses. A value that contradicts another
  setting is refused.
- **Use the default** removes the value set here; the default (or the server's value)
  applies again.
- A setting **locked by the server operator** has no form: change it in the server's
  environment, or ask the operator.
- The **log level** always falls back to the default by itself, after at most 24 hours. Enter
  after how many hours. Personal data stays masked in the log at every level.

You need a recent login to change a setting. Every change is recorded in the audit log with
the old and the new value; a refused change is recorded too.

## Notes

- **Outside the portal bounds**: the server operator set a value the portal itself would not
  accept, such as a very short retention period. It applies, but check with the operator that
  it is intended.
- **Stored value breaks the rules and is ignored**: a stored value is no longer valid, for
  example because it was edited outside the portal. The default or the server's value applies
  instead.
