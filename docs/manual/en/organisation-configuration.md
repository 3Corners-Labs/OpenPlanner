# Configuration: the organisation as a JSON document

This screen exports the configuration of one organisation as a JSON file and reads such a file
back in. Open it through **Organisations → organisation → Overview → Configuration**. It is for
organisation admins and the superuser, and every change needs a recent login.

A document is easy to edit by hand and stays valid across versions: objects are named by their
own keys (a group by its address name, a provider by its key), never by numbers. It holds
configuration only: no bookings, no participants and no staff. You can keep it in version
control, change a value in an editor and read it back in, or copy a team from a test
installation to a production one.

## Exporting

Choose **Export document** to download the organisation's configuration. The file holds only
what you could change yourself on this installation: objects and fields that need a right you
do not hold are left out.

Tick **Include secrets** to take passwords and keys along. They are sealed under the
passphrase you give, which must be at least 16 characters and is asked twice. The passphrase
is stored nowhere: without it the secrets in the file are worthless, and nobody can recover it
for you. Without the tick the file holds no secret and needs no passphrase.

## Setting up a new installation

An export with secrets sets up a base installation without typing everything again: the
integrations (SMTP, SMS, Webex and contact center with their addresses), organisations, groups,
texts and rules.

1. Export on the source installation with *Include secrets* and a passphrase (at least 16
   characters). Keep the passphrase apart from the file.
2. Read the file in on the new installation with the same passphrase: in the portal under
   **Advanced settings → Configuration package**, on the host with
   `python -m app.cli config apply config.json --passphrase-file pass.txt`, or with the
   installer (`scripts/install.sh --configuration config.json --passphrase-file pass.txt`).

You need no separate tool to open the file: OpenPlanner opens each secret itself when it reads
the file in and stores it under the installation's own key. In the file you see a `seal` block
and values like `{"sealed": "..."}`; an export without secrets has neither. A secret cannot be
written into the file by hand: enter it in the portal and export again.

## Reading a document in

Choose the file and **Show plan**. Nothing changes yet. The plan lists, for each object, what
would be created, changed or deleted and which fields, never their values. Read it before you
continue:

- A **refusal** (for example a value outside its bounds, an object that is still in use, or
  a right you do not hold) stops the whole document; fix the file and upload it again.
- A **warning** names what deserves a second look: deleting objects cannot be undone, and
  changing a claim value (the key of an organisation or group, or a role's claim value) locks
  out the people who match the old value until the identity provider sends the new one.
- A document holding sealed secrets asks for its passphrase again when you apply.

Choose **Apply plan** to write everything in one go, or nothing if any part fails. The plan
you applied is the plan you read: if the configuration changed in the meantime, or the page
was open longer than the retention setting allows, upload the file again. The processes pick
up the new settings within ten seconds. Every applied change is in the audit log, without
values.

A document of another organisation, or one that describes the whole platform, is refused here;
the platform's document is on **Advanced settings → Configuration package**.
