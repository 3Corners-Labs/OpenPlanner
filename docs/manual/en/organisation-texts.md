# Texts for participants

An organisation can change the texts that participants see on the pages of its groups and in
emails and text messages. You open the screen with **Organisation settings** in the menu: choose
the organisation and then **Change texts**. Organisation admins change the texts of their own
organisations; the superuser can change those of any organisation.

A text you change here applies to all groups of the organisation. The order is:

1. a group's own text, set in [the group's message texts](group-settings.md);
2. the organisation's text, set on this screen;
3. the OpenPlanner default text.

## What you can change

- **Messages**: the subject and introduction of each email (confirmation, change, reminder,
  cancellation, request to rebook), the text messages, the reasons a planner can choose and
  the closing line. These are the same texts as for a group.
- **Pages for participants**: the introduction on the group's page, the card for a direct call,
  and the explanation after booking and on the page to confirm an appointment.

Other texts, such as buttons, form labels and error messages, cannot be changed.

## Changing a text

First choose the language. An organisation can have its own texts for each language. Change the
text and choose **Save**; the change applies within about five seconds to new pages and new
messages. Emails that were already sent do not change. Empty a field, or put the default text
back in it, to get the default text back.

Below each field you see which placeholders you can use, such as `{date}` or `{deadline}`.
OpenPlanner fills in the date or the deadline there. You may leave a placeholder out, but not
use another one. A text has a maximum length; a subject or title has no line breaks.

Texts are plain text. Markup such as `<b>` or code is not run but shown as written. Do not put
personal data in a text: all participants see the same text.

A change needs a recent sign-in. Every change is in the audit log with the language and the
names of the changed texts, not the texts themselves.
