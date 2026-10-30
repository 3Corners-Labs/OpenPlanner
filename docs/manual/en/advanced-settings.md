# Advanced settings

This screen shows how the installation is configured and where each value comes from. Open it
with **Advanced settings** in the menu. The superuser and the Compliance and privacy role can
view it. The superuser can change some of the settings; the auditor only reads.

Secret values, such as passwords and keys, are never shown: only whether they are set.

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
- **Own certificate (uploaded)**: the proxy serves the certificate uploaded here, for example a
  PKIoverheid or other organisation-validated certificate. Renewal is then yours: upload the
  new pair before the old one expires; the finding *expiring soon* above is the reminder.

To use an own certificate, first upload two PEM files under **Upload or replace the own
certificate**: the certificate chain (server certificate first, then the intermediates) and
the matching private key without a password. The upload is checked before anything is stored:
both files must be readable, the key must belong to the server certificate, the certificate
must be valid for the public address of this installation, and it must be neither expired nor
not yet valid. A refusal names the reason at the field. The private key is stored encrypted and
never shown again; the screen shows the certificate's fingerprint, subject, names, expiry date
and when it was uploaded. Then choose **Own certificate (uploaded)** as the source and save.
The proxy switches within about half a minute; press **Check now** to see the result.

Switching back to **Let's Encrypt (automatic)** takes effect the same way. The uploaded
certificate stays stored for later; **Remove the uploaded certificate** deletes it and its key,
and is possible only while Let's Encrypt is the source.

Every upload and removal is recorded in the audit log with the certificate's fingerprint and
expiry date, never the key. You need a recent login for any of these changes.

When the server operator manages the certificate on the server itself, the setting is **locked
by the server operator** and there is no upload form. When HTTPS ends at a reverse proxy or
load balancer of your own, the screen says the source does not apply: the certificate is
managed there, and the block above still shows what visitors see.

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

- Enter the new value under **Change** and choose **Save**. The portal only accepts values
  within its bounds, for example 1 to 30 bookings per hour per address. A value outside them
  is refused at the row, with the reason.
- Some settings depend on each other: critical storage must stay below low storage, and the
  reminder to rebook must come before the request lapses. A value that contradicts another
  setting is refused.
- **Use the default** removes the value set here; the default (or the server's value)
  applies again.
- A setting **locked by the server operator** has no form: change it in the server's
  environment, or ask the operator. **Manage this here from now on** copies the server's value
  into the portal; then ask the operator to remove the variable named on the row and restart.
  Until then the server's value still applies.
- **Passwords and tokens** (the mail server's password, the update check's read token) are
  write-only: the screen only says whether one is set. Leave the field empty to keep the
  current one; **Use the default** removes it. They are stored encrypted and need the server's
  `SETTINGS_ENCRYPTION_KEY`.
- The **log level** always falls back to the default by itself, after at most 24 hours. Enter
  after how many hours. Personal data stays masked in the log at every level.

You need a recent login to change a setting. Every change is recorded in the audit log with
the old and the new value; a refused change is recorded too.

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
