# Audit log

The audit log shows what was done in the admin portal and what the platform did on its own.
You open it with **Audit log** in the menu. You only see your own level: a group admin their
own groups, an organisation admin and the Compliance and privacy role also their own
organisation, the superuser everything.

The log never contains citizens' data: no names, email addresses or phone numbers.

## What a row shows

- **When**: the time in the portal's time zone, with the same time in UTC below it. That UTC
  time is what you find in exports and on the log platform.
- **Who**: the admin, *System* for what the platform did itself, or *not signed in*.
- **What**: the action, with what changed (field names only, never values), the booking it
  concerns and the link **Whole chain**.
- **Outcome**: success, denied, failed, will be retried or skipped, with the reason or error
  code.

## Filtering

- **Minimum level**: for example *Warning* shows warnings, errors and critical entries.
  Denied and retry are warnings, failed is an error.
- **Kind**: *Admin actions* (logins, changes, denials), *System* (messages, video calls,
  removal of personal data, storage, the automatic integration check), *Security* (rejected
  webhooks, failed Webex token renewal) and *Diagnostics*.
- **From** and **Up to and including**: whole days in the portal's time zone.
- **Trace id**: every row of one request or one job. **Whole chain** on a row takes you
  there too.
- **Group**, **Appointment number** and **Problems only** work as you would expect.

An unknown or malformed value is ignored.

## Exporting

**Export as CSV** downloads exactly what the filter shows now, within your own level.
Organisation admins, the Compliance and privacy role and the superuser can export; group
admins cannot.

- The file opens in Excel with the right characters and columns (UTF-8, semicolon).
- The columns are the fields of the standard event schema; times are in UTC.
- Without a period you get the last 366 days. A longer period or more than 100,000 rows is
  refused: narrow the filter.
- Every export is itself an entry in the audit log, with the number of rows.

An export contains the identifiers (subjects) of admins. Keep the file according to your
organisation's policy.

## Diagnostic mode

Only the superuser sees the **Diagnostic mode** block at the top. Choose a group or a whole
organisation and click **Switch on**. While the mode is on, the platform writes extra entries
of the kind *Diagnostics* about that group, for example *Job run (diagnostics)* with the kind
of job, the attempt and the duration. The group's admins see those entries too.

The mode ends by itself after 24 hours (adjustable), with **Switch off**, or when the disk is
nearly full. Switching on and off is logged.

## How long entries stay

- Admin actions: 2 years.
- System: 90 days.
- Security: 1 year.
- Diagnostics: 7 days.

The operator of the installation can change these periods. Older entries are then removed for
good.
