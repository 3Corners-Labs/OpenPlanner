# Channels

On this screen the superuser records, per organisation, how messages to participants leave the
building: the mail server (SMTP) for email and the SMS gateway for text messages. You open it
through **Organisations → organisation → Participants → Channels**. Passwords and API keys are write-only: the
field is always empty, and an empty field keeps the stored secret (ADR 0022).

## Email

Enter the organisation's mail server: host, port, security (STARTTLS on 587, TLS on 465) and,
when the server asks for it, a user name with password. A login without STARTTLS or TLS is
refused: the password would travel readable over the line. In production a server without
STARTTLS or TLS (*none*) is refused as well: the mail holds names, addresses and join links. Only
the server operator can allow an exception, with the host setting `SMTP_PLAINTEXT_RELAY_ALLOWED`;
an older server of that kind is shown as an error on the overview until it is changed.

The **sender address** is what participants see in their inbox. Use an address in the
organisation's own domain, for example `appointments@city.example`, with a **sender name**
such as "City of Demo, appointments". The **reply address** is where a participant's reply ends
up; leave it empty when the sender address itself is staffed. The **DKIM selector** is only
needed for the check below; ask your mail administrator for it.

Without settings of its own, the organisation's email goes through the installation's mail
server, if the server operator has configured one.

## Test email

A test email goes through exactly the server the system uses for this organisation. When it
succeeds, the server accepts the mail; that says nothing yet about the participant's inbox. When it
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

For domains of organisations with a strict mail policy SPF, DKIM and DMARC with `p=reject` are mandatory (Forum
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

Choose the provider, enter the API host and the API key, and the sender participants see: a name of
at most eleven characters or a registered number. The country code applies to numbers entered
without one.

## Contact center

Only the superuser sets this. An organisation can connect its own contact center, also when that
is another environment (tenant) than the installation's. Choose the **Platform**, enter the **API
address** of your region (https only), the Service App's **Client ID** and its **Client secret**.
The **Refresh token** is the token the Service App received when your environment's administrator
authorised it. The secrets are stored encrypted and never shown again; an empty field keeps the
stored secret.

After saving you see this organisation's **Webhook address**. The contact center reports the
status of tasks there. OpenPlanner checks every report with a secret the server makes itself;
nobody types or sees it. **Make a new webhook secret** replaces it, for example when you suspect
it leaked; the old one stops working at once. **Remove the own connection** erases the connection
and its secrets.

As long as an organisation has no connection of its own, it uses the installation's. That
fallback goes away in a later version. With an own connection this organisation's tasks go only
through it; while the client secret or the refresh token is missing, they wait until the
connection is complete. An API address on this server or in an internal network is refused. The
On saving, OpenPlanner registers the webhook address and its secret with the contact center, and
again after **Make a new webhook secret**. If that fails, the message at the top of the screen
says so; check the Service App's rights (cjp:config_write), then make a new webhook secret.
Connect or disconnect an organisation when none of its tasks is open: a task made through the
previous connection is not ended through the new one.

### Pause deliveries

Use the **Pause deliveries** button on the card to **pause** this organisation's webhook address,
for example while you change the platform's flows. It is a button of its own, not a field of the
form above; **Resume deliveries** puts it back. A paused address still checks every call exactly as
before (network, signature, time): a call that would be refused is refused. A call that checks out
gets the usual `204`, so the platform keeps its subscription and does not switch it off, but its
event is dropped: nothing is recorded on the booking and nothing is remembered, so the platform's
redelivery after you resume is handled. The card shows a *Paused* note while it lasts and counts the
events it dropped, and the status screens (*Integrations* and the overview's status widget) show the
address as paused: the *last event* there stands still, which is neither a healthy nor a quiet
connection. Pausing or resuming changes neither the webhook secret
nor the registration at the platform.

Resuming does not replay what was dropped. The states of the open tasks are brought back by the
periodic check of the tasks (it runs on a schedule and cannot be started from the card), so a
state may take a while to catch up.

<!-- field: cc_allowed_networks -->
### Allowed source networks

The networks the contact center's webhooks to this organisation's address may come from, one per
line in CIDR notation (for example `203.0.113.0/24`; a single address is allowed too), at most 32.
Ask the contact-center supplier for its current ranges. The list **replaces** the installation's
default for this connection; empty, the installation's default applies, and without a default any
source may call.

The card always says which of three states the address is in: *not restricted* (any source may
call; the signature still decides), *restricted to N networks* (from this connection's own list or
the installation's default), or *a list is required and missing* (the host demands a list, so the
address accepts no call until one is set). A list is defence in depth, not the lock: the supplier's
ranges are shared by all its customers, so every call from inside the list still has to carry a
correct signature, and a list that has gone stale refuses the supplier itself. Keep it current when
the supplier announces new ranges.

A list can only be evaluated while the proxy in front of OpenPlanner hands on the caller's address
correctly. When the proxy trust `FORWARDED_ALLOW_IPS` is `*` or wider than one host, the card says
so in red and calls to this address are refused until the host repairs it. A single call whose
address cannot be trusted (forwarded headers its peer was not trusted to send) is refused on its
own; the repair is in the runbook, *Webhooks refused for their network*. Saving only the list
changes neither the webhook secret nor the registration at the platform.

Three set-ups cannot use a list: a proxy that is trusted by a whole network instead of a single
address (a Kubernetes ingress, whose pods change), a content delivery network in front of the proxy
(the caller becomes its address, and listing it would admit all its customers), and a supplier that
calls a public name from a private address. For these, leave the list empty and rely on the
signature, or ask your platform administrator.

A network that covers a whole address family (`0.0.0.0/0` or `::/0`, or an IPv6 network such as
`::/1` that also holds every IPv4 address written as IPv6) is refused: that is no list.
If the stored list cannot be read, the card says so and the address accepts no call until you
save the list again.

**After you set a list, send a test delivery** and check on the card, under *Deliveries seen*, that
it is accepted. A list that locks the supplier out shows there as a refusal for the network.

### Deliveries seen

Under the pause the card shows what OpenPlanner saw at this address: *Last accepted*, *Last refused*
with its reason in words, the number of calls in the current window per outcome (accepted, accepted
but already seen, verified and dropped while paused, verified but about work this connection did
not create, and each reason for a refusal), the faults of single calls whose source address could
not be trusted, by kind, and how many refusals came from inside your own list of networks. Use it
to see whether the supplier gets through and why a call was refused.

These numbers are of the running web process since the window began (the security-event window, a
setting of the platform administrator). A restart empties them and each process counts for itself,
so a quiet card after a restart means nothing. The last times stay while the process runs.

When more than the platform's *refusal alert* number (20 by default) of the following happen at
this address inside the window, one system event tells you: a delivery that was signed but is
too old, a refusal for a call from inside your own list of networks, a refusal for the network
after a delivery was accepted (your list may be out of date), or an event about work this
connection did not create (the subscription may point at the wrong address). Calls from strangers
that fail are counted on the card and raise nothing.

## What is recorded

Every change goes to the audit log with the names of the changed fields, never the values. Of
a test email the log holds the outcome, not the address; of a domain check the verdict, not the
records.

## Alerts to administrators

Where OpenPlanner sends alerts when something goes wrong with this organisation's connections or
its groups' tasks: an administrators' email address or a Webex space. Per address you choose
**From**: all warnings and outages, or outages only. At most ten addresses. **Send a test
alert** checks an address at once.

A lasting alert comes again after the repeat interval (setting *Send an alert again after*,
four hours by default); in between OpenPlanner only counts, and the next message says how often.
When sending fails, OpenPlanner tries again after 1, 5 and 15 minutes. A message holds only codes,
the organisation, the group, the time and a link into the portal: never participants' data.

**Email** goes through the mail channel above (or the platform's fallback).

**Webex space**: create a bot in the Webex developer portal (only the right
`spark:messages_write`), add the bot to the space, paste the bot's token under **Webex bot** and
enter the space's id as the address. The token is stored encrypted and never shown again. Step by
step, with a group's fallback to its own queue: see
[Setting up the contact center](contact-center-setup.md), steps 10 and 11.
