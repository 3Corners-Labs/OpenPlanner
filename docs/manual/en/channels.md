# Channels

On this screen the superuser records, per organisation, how messages to citizens leave the
building: the mail server (SMTP) for email and the SMS gateway for text messages. You open it
through **Integrations → organisation → Channels**. Passwords and API keys are write-only: the
field is always empty, and an empty field keeps the stored secret (ADR 0022).

## Email

Enter the organisation's mail server: host, port, security (STARTTLS on 587, TLS on 465) and,
when the server asks for it, a user name with password. A login without STARTTLS or TLS is
refused: the password would travel readable over the line.

The **sender address** is what citizens see in their inbox. Use an address in the
organisation's own domain, for example `appointments@city.example`, with a **sender name**
such as "City of Demo, appointments". The **reply address** is where a citizen's reply ends
up; leave it empty when the sender address itself is staffed. The **DKIM selector** is only
needed for the check below; ask your mail administrator for it.

Without settings of its own, the organisation's email goes through the installation's mail
server, if the server operator has configured one.

## Test email

A test email goes through exactly the server the system uses for this organisation. When it
succeeds, the server accepts the mail; that says nothing yet about the citizen's inbox. When it
fails, the page says why, in its own words and without the server's answer.

## Sender domain

Whether an email lands in the inbox or in the spam folder is decided by receivers (Gmail,
Outlook, the Dutch providers), mostly on the DNS records of the domain in the sender address.
The button **Check sender domain** reads those records and gives a verdict per record:

| Record | What it is | In order when |
|---|---|---|
| SPF | Which servers may send on behalf of the domain | There is one record ending in `-all` or `~all` |
| DKIM | The signature the mail server puts on every message, with the public key in DNS | The key of the entered selector is found |
| DMARC | What a receiver does when SPF and DKIM do not align, and where it reports that | The policy is `quarantine` or `reject` |

The verdicts mean:

- **OK**: the record is there and well-formed.
- **Attention**: the record is there but does not protect. For SPF it ends in `+all` or
  `?all`; for DMARC the policy is `none` (report only).
- **Missing** or **Invalid**: ask your mail administrator to add or check the record.
- **Could not determine**: the DNS lookup failed. Try again later; if it stays that way, warn
  the server operator.
- **Not checked**: only for DKIM, as long as no selector is entered.

For Dutch government domains SPF, DKIM and DMARC with `p=reject` are mandatory (Forum
Standaardisatie, "comply or explain"). The independent test for that is **internet.nl**; use it
as the final check.

### What the check does not see

The check reads DNS only. It does not see whether your mail server really signs the mail, nor
with which domain. That is the pitfall: DMARC passes only when the domain the server signs with
(or the domain SPF checks) equals the domain of the sender address. If you send
`appointments@city.example` through a provider that signs for `provider.example`, the mail
lands in spam however good the records are. So ask your mail administrator two things: that the
server signs with the domain of the sender address, and which selector it uses for that.

It also helps to keep one fixed sender per organisation (never switch), short and predictable
subject lines, and, when problems persist, to have the mail administrator read the DMARC
reports.

## SMS

Choose the provider, enter the API host and the API key, and the sender citizens see: a name of
at most eleven characters or a registered number. The country code applies to numbers entered
without one.

## What is recorded

Every change goes to the audit log with the names of the changed fields, never the values. Of
a test email the log holds the outcome, not the address; of a domain check the verdict, not the
records.
