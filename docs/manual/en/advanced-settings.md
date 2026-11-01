# Advanced settings

This screen shows how the installation is configured and where each value comes from. Open it
with **Advanced settings** in the menu. The superuser and the Compliance and privacy role can
view it. The superuser can change some of the settings; the auditor only reads.

Secret values, such as passwords and keys, are never shown: only whether they are set.

The screen has three tabs under its title:

- **System status**: the environment, the certificate and the data fields;
- **Settings**: every platform setting by topic, with *On this page* at the top, a list of links
  to each topic, and the trial of the login mapping;
- **Configuration package** (the superuser only): export and import.

A change or an error shows on the tab of the form you used.

## System status

The settings that only the server operator sets, in the server's environment or in a file.
They are needed before the portal or the database works, or they belong to the server itself.
For each one you see whether it is set and where it comes from, never its value.

When the installation does not run as production (for example a test environment), the
screen also lists what production would refuse: a missing key or an address without https.
Production never starts with any of these.

### Certificate of the public address

Below the environment is the certificate visitors reach the installation with: the address,
who issued it, until when it is valid (with the days left), when it was last checked and one
finding:

- **Fine**: the certificate is trusted and valid for a good while. Renewal happens on its own.
- **Expiring soon**: fewer days are left than the setting *Warn when the certificate is valid
  for fewer than*. Automatic renewal seems to be failing; tell the server operator.
- **Not trusted**: the certificate comes from a test, private or unknown certificate
  authority. Normal during a trial installation with Let's Encrypt's staging environment; in
  production browsers warn visitors.
- **Expired**, **wrong name**, **not yet valid**, **unreachable**, **no https**: the text next
  to the finding says what is going on.

The server checks the certificate every few hours (setting *Check the certificate every*). A
finding other than *fine* is also in the audit log as a system event `tls.certificate`, repeated
while it lasts (setting *Repeat the certificate report every*). *Not checked yet* means the
server has not run a round yet.

**Check now** (superuser) checks the certificate at once instead of waiting for the next
round, for example after changing the source below.

Support may ask for a screenshot of this screen instead of the server's configuration.

### Certificate source

On a small installation the bundled HTTPS proxy serves the certificate, and the superuser
chooses where it comes from:

- **Let's Encrypt (automatic)**, the default: the proxy obtains the certificate itself and
  renews it; nothing to do.
- **Own certificate**: the proxy serves the certificate placed here, for example a PKIoverheid
  or other organisation-validated certificate. Renewal is then yours: place the new chain
  before the old one expires; the finding *expiring soon* above is the reminder.

The private key of an own certificate is made on this server and never leaves it: it is not in
the database, not in a backup of it, and never shown. To get an own certificate:

1. Choose **Make a new key and request**. The screen shows a certificate signing request (CSR)
   for the public address, as text and as a download. A request does not disturb a certificate
   already in use; a new request replaces an earlier one.
2. Hand the request to your certificate authority. You receive a certificate chain (PEM).
3. Place the chain under **Place or replace the certificate chain** (server certificate first,
   then the intermediates). The upload is checked before anything is stored: the file must be
   readable, the chain must belong to the key made here (or to the key already in use, for a
   renewal), the certificate must be valid for the public address of this installation, and it
   must be neither expired nor not yet valid. A refusal names the reason at the field. The
   screen then shows the certificate's fingerprint, subject, names, expiry date, when it was
   placed, and that its key is present on this server.
4. Choose **Own certificate** as the source and save. The proxy switches within about half a
   minute; press **Check now** to see the result.

To migrate an existing certificate once, place the chain and, in the second field, its
private key together; the key goes straight to the proxy's storage on this server and is not
kept anywhere else.

Switching back to **Let's Encrypt (automatic)** takes effect the same way; the certificate and
the key stay for later. **Remove the uploaded certificate** deletes the certificate (never the
key) and is possible only while Let's Encrypt is the source.

**Private key missing on this server** appears when the database was restored on another
server: the certificate came along, the key did not. Make a new request and place its chain,
or migrate the pair again; until then the proxy uses Let's Encrypt.

Every request, upload and removal is recorded in the audit log with the certificate's
fingerprint and expiry date, never a key. You need a recent login for any of these changes.

When the server operator manages the certificate on the server itself, the setting is **locked
by the server operator** and there are no forms. When HTTPS ends at a reverse proxy or load
balancer of your own, the screen says the source does not apply: the certificate is managed
there, and the block above still shows what visitors see.

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
sessions, staff and the planner, the fallback mail server, the update check and the Webex
Service App with the WxCC host. The webhook secrets (generated by the server) and the login
mapping follow in a later version. A changed Webex value connects the video calls and work
items again within about ten seconds. Set the client id, client secret and refresh token of a new Service App together.

- Each topic is one form with one **Save** button at the bottom. Enter the new value at the
  settings you want to change (on or off is chosen from a list) and choose **Save**; only the
  values that differ from the current ones are saved. The portal only accepts values within
  its bounds, for example 1 to 30 bookings per hour per address. A value outside them is
  refused at the row, with the reason; the values above it in the same form are saved by
  then, the ones below it not yet.
- Some settings depend on each other: critical storage must stay below low storage, and the
  reminder to rebook must come before the request lapses. A value that contradicts another
  setting is refused.
- **Use the default** removes the value set here; the default (or the server's value)
  applies again.
- A setting **locked by the server operator** has no field: change it in the server's
  environment, or ask the operator. **Manage this here from now on** copies the server's value
  into the portal; then ask the operator to remove the variable named on the row and restart.
  Until then the server's value still applies.
- **Passwords and tokens** (the mail server's password, the update check's read token) are
  write-only: the screen only says whether one is set. Leave the field empty to keep the
  current one; **Use the default** removes it. They are stored encrypted and need the server's
  `SETTINGS_ENCRYPTION_KEY`.
- The **log level** always falls back to the default by itself, after at most 24 hours; the
  level TRACE after at most the hours of *TRACE_LEVEL_MAX_HOURS* (4 by default). Enter after
  how many hours. Personal data stays masked in the log at every level; TRACE lines carry only
  the fixed fields of `docs/logging.md`.

You need a recent login to change a setting. Every change is recorded in the audit log with
the old and the new value; a refused change is recorded too.

## Configuration package

A configuration package holds every setting of this installation: organisations, teams,
opening hours and exceptions, reminders, texts, form fields, contact options, holiday
calendars, closure scenarios, branding, email and SMS channels, identity providers, API
clients, roles and the platform settings, with their secrets. Bookings, slots, the audit log
and staff stay behind. It sets up a new installation (test or production) in one go, without
entering every password and key again.

**Export.** Choose a passphrase of at least 16 characters and repeat it. You get a file
`openplanner-configuration-….sqlite`. The secrets in it are encrypted with the passphrase,
which is stored nowhere. Without the passphrase the package is worthless; with it, it holds
every secret of the platform. Keep the file and the passphrase apart, and no longer than you
need them.

**Import.** Choose the file, enter the passphrase and tick that every setting of this
installation will be replaced. That is possible only in an installation without bookings,
slots or an active closure: a new one. Staff and the audit log stay. The package must be from
the same version of OpenPlanner; if it is not, export it again from an installation on this
version. After the import the settings apply within ten seconds. The server's bootstrap keys
(`SETTINGS_ENCRYPTION_KEY` and the other two) never travel: every installation keeps its own.

Every export and import is in the audit log, without the passphrase and without values. You
must have logged in recently. The largest package allowed is the platform setting *Largest
configuration package*.

**Through the API.** A pipeline can do the same without the portal: an API client with the
scope *Export and import the configuration package* (`configuration:transfer`, granted on
**API clients**) fetches and places the package with `GET` and `PUT
/api/agent/v1/configuration`, the passphrase in a header. That scope covers the whole
installation, whatever organisation and teams the client has; grant it only to a client that
provisions installations, never to a bot. The same rules and the same audit log apply, with
the client id as actor.

## Changing the login mapping

Only the superuser can change which claims of the identity provider carry the roles,
organisations and groups, and which value is which role. Fill in **Change the login mapping**
and choose **Test with a new login**. You are sent to the identity provider to log in again.
The new mapping is applied to that login: it is saved only if it still makes you superuser.
Otherwise nothing changes and the screen says so. This keeps anyone from locking everybody out
by mistake.

When a value is locked by the server operator, the whole mapping is theirs and the form is not
shown. If the identity provider changes a claim or role value later, the server operator can
put the mapping right from the server.

## Notes

- **Outside the portal bounds**: the server operator set a value the portal itself would not
  accept, such as a very short retention period. It applies, but check with the operator that
  it is intended.
- **Stored value breaks the rules and is ignored**: a stored value is no longer valid, for
  example because it was edited outside the portal. The default or the server's value applies
  instead.
