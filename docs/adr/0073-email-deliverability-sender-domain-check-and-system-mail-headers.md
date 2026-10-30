# 0073. Email deliverability: the sender domain checked in the portal, system mail marked as such

Status: accepted (2026-10-30), partly built (step 1 merged in #166, step 2 follows), requested by the product owner ("how to prevent email hitting
spam folders"). Extends ADR 0022 (message channels per organisation) and ADR 0009 (message
layouts); grants a licence exception in the manner of ADR 0003. The *Build status* table
below says what is built.

## Context

Every confirmation, reminder and cancellation leaves OpenPlanner as an email through the SMTP
server of the organisation (ADR 0022), or through the installation's fallback server. Whether
that mail reaches the citizen's inbox or the spam folder is decided mostly outside this code:

- **Domain authentication.** Receivers (Gmail, Outlook, the Dutch providers) judge mail by the
  DNS records of the domain in the `From` address: SPF (which servers may send for it), DKIM
  (a signature the relay puts on, with a public key in DNS) and DMARC (what to do when neither
  aligns with the `From` domain, and where to report). Since February 2024 Gmail and Yahoo
  refuse mail from senders without SPF or DKIM, and demand all three plus a working reverse
  DNS of bulk senders. For Dutch government domains SPF, DKIM and DMARC with `p=reject` are on
  the "pas toe of leg uit" list of the Forum Standaardisatie, and `internet.nl` tests them.
- **Alignment.** DMARC passes only when the domain the relay signs with, or the domain in the
  envelope sender that SPF checks, is the `From` domain. An organisation that enters
  `afspraak@gemeente.nl` as sender but relays through a provider that signs for
  `provider.example` fails DMARC, and finds out only when citizens complain.
- **The message itself.** Transactional mail needs no `List-Unsubscribe`, but it should say
  what it is: `Auto-Submitted: auto-generated` (RFC 3834) tells autoresponders and filters
  that nobody wrote it, a `Reply-To` tells the citizen (and the receiver) that a reply goes
  somewhere, and a display name in `From` beats a bare address.

What the adapter already does right: `Message-ID` in the sender's own domain, a `Date`
header, always a plain-text part next to the HTML, no tracking pixels, a fixed sender per
organisation. What it lacks is `Auto-Submitted` and `Reply-To`, and the portal has no way to
tell the superuser that the sender domain is not set up, before the first citizen is affected.

The superuser configures the SMTP server per organisation and can send a test email (ADR
0022). That test proves the server accepts the mail; it says nothing about the inbox.

## Decision

1. **System mail is marked as such.** Every email the adapter builds carries
   `Auto-Submitted: auto-generated`. When the organisation has set a reply address, the mail
   carries `Reply-To`. `From` keeps the display name of ADR 0022. No `List-Unsubscribe` and no
   `Precedence: bulk`: the mail is transactional, one message per booking event, and an
   unsubscribe would contradict the citizen's own booking.

2. **Two new fields on the SMTP settings** (ADR 0022, *Channels* tab of the organisation,
   `integrations.manage`, superuser only): the **reply address** (`reply_to_address`, optional,
   same validation as the sender address) and the **DKIM selector** (`dkim_selector`,
   optional, one or more DNS labels such as `s1` or `mail2024`). Both are non-secret settings in
   the same JSON as the host and the sender; no migration. Saving them is audited by field name
   like every channel setting.

3. **A sender domain check on the *Channels* tab.** A button *Check sender domain* looks up,
   for the domain of the sender address in effect (the organisation's own SMTP settings, else
   the installation's fallback sender):

   | Record | Looked up at | Verdict |
   |---|---|---|
   | SPF | `TXT` of the domain | *ok* with `-all` or `~all`; *warning* with `+all` or `?all` (anyone may send); *missing*; *invalid* (two SPF records, or an unparsable one) |
   | DKIM | `TXT` of `<selector>._domainkey.<domain>` | *ok* with a public key; *invalid* when the key is empty (revoked) or the record is not a DKIM record; *missing*; *not checked* without a selector |
   | DMARC | `TXT` of `_dmarc.<domain>` | *ok* with `p=quarantine` or `p=reject`; *warning* with `p=none` (monitoring only); *missing*; *invalid* (two records, or no `p=`) |

   A lookup that fails (no answer from the resolver, timeout) gives *unknown* for that record,
   never a guess. The page shows one line per record with its verdict and an explanation in
   the admin's language, and the overall finding (the worst verdict) as a chip. The check runs
   on demand only, from the superuser's fresh session with the Fetch Metadata check, rate
   limited like the test email, and is audited as `smtp.domain_check.<finding>`; the records
   themselves are not stored. The check reads DNS; it proves nothing about the relay's
   signature, so the manual says what the verdicts mean and what to ask the mail provider.

4. **The lookups use the host's resolver through `dnspython`.** The standard library has no
   TXT lookup; a hand-written DNS client would have to parse untrusted answers and handle
   truncation and TCP fallback. `dnspython` is the de-facto Python DNS library, actively
   maintained, with the ISC licence, which is functionally BSD-2-Clause but not on the
   allow-list of `CLAUDE.md`. **This ADR allows `dnspython` as the single ISC runtime
   dependency**; `scripts/check-licenses.sh` lists it in `IGNORED` with a reference here, and
   `docs/dependencies.md` records the review. Any further exception needs its own ADR.

5. **Not changed, on purpose.** The citizen's `.ics` stays an attachment with
   `METHOD:PUBLISH`: an inline `text/calendar; method=REQUEST` would make every confirmation
   an invitation with accept/decline buttons that answer to nobody (the team invite of ADR
   0043 is a request, because the team's mailbox is the organiser). Bounces are not processed:
   the adapter already distinguishes a refused recipient from a server failure (ADR 0022) and
   PII is purged after the slot; a bounce mailbox would hold addresses outside that rule.
   Warm-up, feedback loops and DMARC report reading are the mail provider's work, named in
   the manual.

6. **Later, when step 1 is in use:** a periodic check in the worker with a system event when
   a verdict worsens, like the certificate watch of ADR 0064. Decided then, not now: it needs
   a stored last result per organisation and a notification path, and the on-demand check
   first has to show which verdicts matter in practice.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `reply_to_address` | D organisation (SMTP settings, *Channels*) | none | a valid address, at most 254 characters | `integrations.manage` | Optional; without it no `Reply-To` header |
| `dkim_selector` | D organisation (SMTP settings, *Channels*) | none | DNS labels (letters, digits, inner hyphens, at most 63 each), at most 253 characters | `integrations.manage` | Optional; without it DKIM is *not checked* |

**Fixed on purpose:** the lookup lifetime of 5 seconds per record (three records, one
operator-triggered page; the same reasoning as the probe timeout of ADR 0064); the header
`Auto-Submitted: auto-generated` on every message (RFC 3834, not a tunable); the set of records
checked (SPF, DKIM, DMARC are the three every receiver evaluates).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Headers, the two fields, the sender domain check on *Channels*, manual topic `channels`, `dnspython` | #166 | merged |
| 2 | Periodic check in the worker with a system event (decision 6) | – | follow-up; decided after step 1 is in use |

(State as checked against the code on 2026-10-30; the overview of every ADR is `docs/adr-status.md`.)

## Consequences

- `app/domain/mail_domain.py` holds the verdicts as pure rules over record texts; the
  resolver is a port (`MailDomainResolver`) with the adapter `app/adapters/mail_dns.py` and a
  fake in `tests/fakes/`. The channel service gets `check_sender_domain`, next to the test
  email.
- `docs/threat-model.md` gets the check's abuse cases (the button used to probe arbitrary
  domains: only the stored sender domain is looked up; a resolver answer that is not a record:
  codes only, never the answer on the page or in a log). `docs/dataflow.md` names the DNS
  queries (domain names only, no personal data).
- The *Channels* screen gets its manual topic (`docs/manual/{nl,en}/channels.md`), which it
  lacked: what each verdict means, what to ask the mail provider about alignment, and that
  `internet.nl` is the independent check.
- `docs/dependencies.md` and `scripts/check-licenses.sh` record `dnspython` (decision 4).
- The authorisation matrix gains one route under the existing permission; `docs/
  authorization.md` and `docs/admin-map.md` do not change (no new screen, no new permission).
