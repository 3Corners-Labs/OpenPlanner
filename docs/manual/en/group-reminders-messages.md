# Reminders and message texts

Two tabs of a group decide what participants receive around their appointment:

- **Reminders**: when a reminder is sent, and by which channel.
- **Message texts**: what the messages say.

Open them through **Groups → group → Reminders** or **Message texts**. Before you save, the
portal may ask you to log in again.

| Tab | Who may change it |
|---|---|
| Reminders | organisation admins and the superuser, and a custom role with the right to manage reminders |
| Message texts | group admins, organisation admins and the superuser, and a custom role with the right to manage texts |

## Reminders

The page lists the group's reminder rules: the channel, the moment, and a status. A rule has the
status **Configured** when its channel can send, and **Not configured** when it cannot. A red
notice above the list says so when at least one rule has a channel that is not set up: those
messages are not sent until a superuser sets up the channel under Integrations (see
[Channels](channels.md)). Email also counts as configured when the platform itself has an email
server set.

Changes apply to new bookings. Reminders already planned for existing bookings stay, so nobody
loses a reminder that was promised. That goes for adding, changing and removing a rule.

### Adding a reminder

<!-- field: channel -->
#### Channel

**Email** or **SMS**. An SMS only goes to participants who gave a phone number. If the group's
booking form does not ask for a phone number, SMS cannot be chosen; the page says so.

<!-- field: amount -->
#### How long, and unit

A whole number and a unit: **minutes before**, **hours before** or **days before** the start of
the appointment. The moment must be between 5 minutes and 30 days before the start. A group can
have at most 5 reminder rules. The same channel at the same moment cannot be added twice; the
portal then says the rule exists.

The list shows the moment in the largest whole unit: 1440 minutes is shown as *1 day before*.

### Changing and removing a reminder

**Change** opens the form with the current channel and moment. If you saved without changing
anything, the portal says so. **Remove** deletes the rule at once. For a change as well, the
channel and moment may not be those of another rule of the group.

## Message texts

On this tab you change the text of the messages to participants. The layout, the links and the
calendar attachment stay fixed, and you do not edit HTML or templates; you only change the text.
The tabs at the top choose the language of the texts you edit; every language the installation
offers has its own set. Participants get the message in their own language.

A text is chosen in this order:

1. the group's own text, set here;
2. the organisation's text, set under [Texts for participants](organisation-texts.md);
3. the OpenPlanner default text.

Under each field you read whether the text is **Default text** or **This group's own text**.

### The messages

The page has a card per kind of message. Every email has a subject and an introduction; most also
have an SMS text.

- **Confirmation**, **Change**, **Reminder** and **Cancellation**.
- **Request to choose a new time (by a planner)**, **Reminder to choose a new time** and
  **Appointment lapsed without a new time**: the messages of a planner's request for a new time
  (see [Group agenda](group-agenda.md)).
- **Reasons for changes by a planner**: the three fixed reasons a planner can choose from (not
  enough agents, technical failure, unforeseen circumstances). Planners cannot type a reason of
  their own.
- **All emails**: the closing line.
- **What you call the person who takes the call**: the word for the agent in this group, in the
  singular and the plural. In a text you use `{agent}`, `{agents}`, `{Agent}` and `{Agents}` for
  it. Both words change together.

### Placeholders

A text may contain words between braces that are filled in when the message goes out, for
example `{date}`. Under each field the page lists the ones that field may use:

- Emails: `{date}`, `{time}` and `{group_name}`.
- SMS: also `{first_name}` and, for most, `{manage_url}`, the link to change the appointment. The
  cancellation SMS has `{reason}` instead of the link.
- Messages about a new time also have `{deadline}`, the moment the request lapses.

You may leave a placeholder out, but you may not use another one. The agent words above work in
every text. Limits: a subject is at most 200 characters on one line, an email introduction at
most 2000 and an SMS at most 320 characters (before the placeholders are filled).

### Saving

Press **Save** to store the texts of the language you see. All fields are checked first: if one
is wrong, nothing is saved and the field says what is allowed (the maximum and the placeholders).
Empty a field, or put back the default text, to bring the default back: only real changes are
stored. Every change is in the audit log with the keys of the texts and the language, not with
the texts themselves.
