# Organisation settings

An organisation can set its own time format, time zone and periods. Open the screen with
**Organisations → organisation → Overview → Settings**. Organisation admins change
the settings of their own organisations; the superuser can change every organisation's.

Without a value of its own, an organisation follows the platform value, which the superuser
sets on **Advanced settings**. The **Source** column says which one applies:
**Organisation** or the platform's (default, admin portal or environment).

The settings are grouped by topic (display, staff, planner, confirmation and so on). *On this
page* at the top jumps to a topic; one *Save* button saves all topics together.

## The settings

- **Time format**: how clock times are written: 24 hours (`15:05`), 12 hours (`3:05 PM`) or as
  the language does it. It applies to the booking pages, the calendar on websites, emails and
  text messages of the organisation's groups, and to its admins' pages.
- **Admin portal time zone**: the time zone of times on the admin pages, for example
  `Europe/Amsterdam`. Each group keeps its own time zone for its appointments.
- **Show "Appointments via OpenPlanner" at the bottom**: whether the booking pages and the
  emails to participants end with the product mark and the line "Appointments via OpenPlanner".
  The organisation's own logo and name stay in the header; the plain-text part of an email
  never carries the line. `true` or `false`.
- **Staff member dormant after**: days without a login after which someone is marked dormant
  on the **Staff** screen.
- **Shortfall look-ahead**: how many days ahead the shortfall notice on group pages looks.
- **Request to rebook lapses after** and **Rebook reminder**: when a planner asks participants to
  choose a new time, how long the request stays open and when the one reminder goes. The
  reminder must come before the request lapses.
- **Details held for a closure deleted at the latest after** (30 to 180 days, default 90) and
  **Notice to administrators before that maximum** (3 to 28 days, default 14): the most days
  after the original appointment that an appointment on hold by an emergency closure keeps the
  participant's details, also while the closure has no end, and how many days before that the
  team's audit log shows the notice. The maximum cannot be switched off.
- **Target wait** (1 to 60 minutes, default 5) and **Alert when the wait exceeds** (2 to 60
  minutes, default 8): for groups with an own queue. The *Queue across my groups* widget on the
  overview says whether the longest wait stays within the target; when it goes above the limit,
  those who work the queue get one alert, which goes when the wait is back under the limit. The
  target may not be above the limit.
- **Change requests by a bot (with appointment code)** and **Time to confirm a change request**
  (10 to 1440 minutes, default 60): whether a contact-center bot may ask, for a caller, to
  cancel or move an appointment. The caller gives the phone number and the eight-digit
  appointment code together; that code is then on the booking page and in the confirmation,
  change and reminder messages. Nothing changes until the participant confirms the request
  through their own email or text message, within the set time. Switched off, the API refuses
  requests and open requests lapse.
- **Quiet period after a change request** (1 to 1440 minutes, default 10): after a request
  reached an appointment, the participant gets no new message for that appointment during this
  period, even if the bot asks again. Cannot be switched off.

## Changing a setting

Enter the new values and choose **Save** at the bottom; only the values that differ from
the current ones are saved, and they apply within about ten seconds. **Use the
platform value** removes the organisation's value. You need a recent login to change a
setting. Every change is recorded in the audit log with the old and the new value.

An admin who belongs to exactly one organisation sees times in that organisation's format and
time zone. The superuser, and an admin of more than one organisation, sees the platform
values.
