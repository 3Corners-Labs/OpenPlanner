# Data fields and personal data

The card **Data fields** on the **Advanced settings** screen lists which fields of an
appointment are personal data of the participant, and how sensitive each one is. The superuser
and the Compliance and privacy role can read it. Nobody can change it: the list is fixed in
the software, so no installation can switch this protection off.

## The fields

Personal data is stored only on the appointment (the booking), never on a team, an
organisation, a message text or a log line.

- **Direct** fields identify the participant on their own: first name, last name, email address
  and phone number.
- **Sensitive** fields hold what the participant told us, or give access to the conversation: a
  note, the answers to the team's own questions on the booking form, and the links to join the
  video call and the trial call (a link for the call itself, one for the host, and one for the
  trial call before it).

The answers to a team's own questions count as personal data unless the question was marked
*not personal data* when it was added (see [the booking form](group-booking-form.md)).

## Where personal data is shown

- **The appointment page** of a team's agenda (behind *Details*) shows the answers to the
  team's questions, for roles that may view appointments.
- **The queue** shows the first name of the participant whose turn it is, and the picked-up
  appointment shows the link to join the call, for agents who work the queue.
- **The agenda** and the planner's pages show the first name only.

Nowhere else: not on the overview, not in cross-team lists, not in the audit log or its
export, not in a search result and not in an alert. A search or filter never matches on a
personal-data field, so a list cannot reveal that someone has an appointment. The
Compliance and privacy role never sees personal data at all.

## Retention

Personal data is removed 24 hours after the appointment ended or was cancelled; see
[Retention](retention.md). A removed appointment keeps only its reference and times.
