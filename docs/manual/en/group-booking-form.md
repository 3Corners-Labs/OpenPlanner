# A team's booking form

The form always asks a citizen for first name, last name and email address. Whether it also
asks for a phone number is set in the [team's settings](group-settings.md). Does the team need
more, such as a customer number or the subject of the conversation? Then add custom fields
here. You open the screen with **team → Settings → Manage the booking form**. Organisation
admins and the superuser can change it.

Ask only what the team really needs for the conversation. Every extra field is data we must
protect.

## What you never ask

The form refuses questions about a citizen service number (BSN), a passport or other identity
document, and about special categories of personal data such as health, religion, origin,
sexual orientation, political opinion, trade union membership or criminal records. That check
is a safety net, not a guarantee: do not choose a question that amounts to one either.

<!-- field: key -->
## Key

The key is the fixed name of the field, for example `customer_number`. It consists of
lower-case letters, digits and `_`, starts with a letter and is 2 to 40 characters long. Once
saved it is fixed, and a key the team used before cannot be used again. So an answer on a
booking always means what the question meant at the time.

<!-- field: type -->
## Type

The type decides how the answer is checked. You cannot write a check of your own.

- **Short text**: up to 100 characters.
- **Free text**: up to 1000 characters. This is always personal data, because a citizen can
  write anything in it.
- **Email address** and **phone number**: checked like the fixed fields; always personal data.
  A phone number without a country code is read as a number of the country the organisation
  set (usually the Netherlands), and stored with its country code.
- **Whole number**: with a lower and upper limit if you give them. A number with leading zeros,
  such as a customer number, is a short text.
- **Date**: with limits in days from the day of booking.
- **Postcode (Netherlands)**: four digits and two letters, stored as `1234 AB`.
- **Choice list**: the citizen picks one of your choices; free text is never stored.
- **Checkbox**: ticked or not. A required checkbox must be ticked, for example for consent.

The type is fixed once saved as well. If you want another type, retire the field and add a new
one.

<!-- field: label_nl -->
## Question and help text

Every question has a text in Dutch and in English. A help text is optional; it appears below
the question. The citizen sees the language of the page, otherwise Dutch.

<!-- field: classification -->
## Personal data

Every new field is personal data. Answers to such a field never appear in logs, the audit log,
exports, messages or the agents' queue. Only the booking's page in the agenda shows them, to
those who may view bookings.

If you choose **Not personal data**, you must confirm that the answers never say anything about
a person. That suits, for example, a choice list with the subject of the conversation. When in
doubt, keep it personal data. If you later set a field back to personal data, that applies at
once to the answers already given too.

However you tag a field: all answers are erased with the rest of the booking's data, 24 hours
after the appointment or after cancelling.

<!-- field: options -->
## Choices

Choice lists only. Put each choice on its own line, as key, Dutch text and English text,
separated by a semicolon:

    rent; Huurtoeslag; Rent benefit
    care; Zorgtoeslag; Healthcare benefit

At most 30 choices, each with its own key.

<!-- field: minimum -->
## Limits

Whole numbers and dates only. For a number the limits are the lowest and highest value. For a
date they are days from the day of booking: `-30` and `0` allow only a date in the past 30
days. Empty means no limit.

## Order and retiring

**Up** and **Down** put the fields in the order the citizen sees them. **Retire** takes a field
off the form. Answers already given stay on the booking until they are erased, and from then on
count as personal data.

A form has at most the number of custom fields the superuser sets (5 by default).

## What is recorded

Every change goes to the audit log with the field's key and what changed, never the text of a
question or an answer.
