# Organisation

An organisation is a client of the installation: it has its own groups, branding, channels
and admins. Everything of one organisation sits on its own page, in tabs. You open it through
**Manage → Organisations**. Whoever may open exactly one organisation (an organisation admin
or an auditor of one organisation) lands on that page at once; the superuser, and whoever
manages several organisations, chooses from the list first.

Which tabs you see depends on your rights. A tab you may not use is not there.

## The list

The **Organisations** list shows the name, web address and default language of every
organisation in your reach. The name opens the organisation's page; **Groups** opens the
Groups list narrowed to that organisation. Only the superuser can create an organisation.

## Tabs

- **Overview**: the organisation's groups you have rights on, and the profile (name, web
  address, default language). The superuser changes the name and language with **Change name
  and language**; the web address is fixed. This tab has three sections:
  - **Settings**: the organisation's time format, time zone and periods
    ([more](organisation-settings.md)).
  - **Texts**: the organisation's own wording of the pages and messages to participants
    ([more](organisation-texts.md)).
  - **Embedding**: which websites may carry the organisation's widget ([more](embedding.md)).
- **Branding**: name, logo, colours and fonts of the appointment pages ([more](branding.md));
  superuser only.
- **Channels**: the organisation's mail server and SMS gateway ([more](channels.md));
  superuser only.
- **Contact**: the alternative contact options participants see during a closure
  ([more](organisation-contacts.md)).
- **Holidays**: which holiday calendars the organisation's groups follow. The calendars
  themselves are kept by the superuser under **Platform → Holiday calendars**.
- **Emergency closure**: close and reopen every team of the organisation at once, and the
  scenarios for it ([more](closure-scenarios.md)).

The path at the top of every page (**Organisations › organisation**) and on a group's page
(**Groups › organisation › group**) shows where you are; every step is a link.

## Deleting an organisation

At the bottom of the edit form sits **Delete organisation…**, for the superuser only (the right
`organisation.delete` is separate from managing organisations). The confirmation page says what
happens and asks you to type the organisation's name; without a recent login the portal first
asks you to log in again.

Deleting is possible only when the organisation is empty: no groups (archived ones included;
they go by themselves after the erase period) and no API clients. The page says what is still
in the way and links to it.

With the organisation go its branding, channels and their secrets, widget websites, contact
options, texts, settings, closure scenarios and identity providers. Staff keep their role at the
identity provider, but a role that names this organisation grants nothing any more. The log keeps
the web address as text. Deleting cannot be undone.
