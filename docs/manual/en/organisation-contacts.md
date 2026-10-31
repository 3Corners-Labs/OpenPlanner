# Alternative contact options

When participants cannot book an appointment, they want to know how else to reach the
organisation. On this screen you record that once for the whole organisation. The block is
shown on the page of an emergency closure and on the maintenance page (ADR 0037 and 0038). You
open it through **Closures → organisation → Manage alternative contact options**. An
organisation admin or superuser can change it.

Set this up before something goes wrong. In an emergency there is no time to look up and
translate numbers.

## A team's own block

A team with its own desk or number can get a block of its own (screen **A team's contact
options**). That block then applies instead of this one. A closure takes a copy of the block
that applies when it starts; later changes do not alter a running closure.

## What participants see

A heading (by default "Other ways to reach us") followed by the contact options in your order.
Phone numbers and email addresses are links that call or mail straight away on a phone. The
preview at the top of the screen shows the block as it is saved now.

Participants see the block in three places, always as the copy that belongs to the closure:

- **During a closure** the team's page shows no calendar but the closure page: the closure's
  heading, the period, the text and this block below it.
- **Before a planned closure** the calendar stays. The notice above it links to the closure
  page, so participants already see how to reach you during the closure.
- **For an appointment on hold** the block is on that appointment's page, above the buttons to
  choose a new time or cancel.

## Contact options

You can fill in at most six. Choose a kind per row and fill in the details:

| Kind | Details |
|---|---|
| Phone | A number of 3 to 15 digits, with spaces, `+`, `-` or brackets, such as `14 020` or `+31 (0)20 624 1111` |
| Email | A general address, such as `info@example.org` |
| Website | A link starting with `https://`, for example to the contact form |
| Chat | A link starting with `https://`, to the organisation's chat |
| Address | One to four lines, such as the name of the building, street and postcode with town |

For each row you can give a **name** (for example "Customer contact centre") and the
**opening hours** (for example "Mon-Fri 9:00-17:00"), in Dutch and in English. Leave the
English text empty when it is the same; an English-speaking visitor then sees the Dutch one.
Without a name the page shows the kind, such as "Phone".

A row that is completely empty is skipped. If you empty every row and save, the block is
removed.

## Public

Everything on this screen is public: anyone who opens the page sees it. Use only general
numbers and addresses of the organisation, never the number or email address of an employee.
Links must start with `https://`; the portal refuses other links.

## What is recorded

Every change goes into the audit log, with the number of contact options per kind. The numbers,
addresses and texts themselves are not in the log.
