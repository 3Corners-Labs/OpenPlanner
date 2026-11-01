# Log destinations

This screen connects the platform's log to a log platform: a syslog server, Splunk, Elastic or
any HTTP endpoint that takes JSON lines. Open it through **Platform → Log destinations**. The
superuser creates, changes, tests, switches on and off and deletes destinations, and needs a
recent login for that; the auditor sees the list and the delivery status, never a token.

A destination is a **copy**. The standard output of the containers keeps working as before,
so `docker compose logs` and a log agent on the host are not affected. A change reaches the
web and the worker within about 15 seconds. Lines are delivered in the background in batches:
a slow or failing platform never slows a booking page or a job. When a platform stays away,
the newest lines are kept up to a limit and the oldest are dropped; the screen counts them.

## Adding a destination

Choose the kind, fill in the address and, when the kind takes one, the token or key. The
destination starts **off and untested**. Choose **Send test line** first: one line goes to the
platform at once and the page says whether it arrived, with the platform's status code but
never its own text. Then choose **Switch on**.

<!-- field: name -->
## Name

A short name for the list, for example `Splunk production`. Names are unique.

<!-- field: kind -->
## Kind

- **Syslog**: RFC 5424 messages, framed with octet counting over TLS (recommended) or TCP, or
  as datagrams over UDP. The JSON line is the message; the application name is `openplanner`
  and the facility `local0`.
- **Splunk (HTTP Event Collector)**: one event per line, with its time, index and source.
- **Elastic or OpenSearch**: the `_bulk` API into a data stream.
- **HTTP (JSON lines)**: a `POST` of newline-delimited JSON, for an OpenTelemetry collector,
  Logstash or Fluent Bit.

<!-- field: address -->
## Address

`https://host/path` for the HTTP kinds; `syslog+tls://host:6514` for syslog (also
`syslog+tcp://` and `syslog+udp://`, which are for development: a production installation
refuses them, and plain `http://`). The address holds no user name, password or query; the
token has its own field. For Splunk the path defaults to `/services/collector/event`.

<!-- field: secret -->
## Token or key

The Splunk HEC token, the Elastic API key or a bearer token. It is stored encrypted, never
shown again and never written to a log or the audit log. Leave the field empty to keep the
stored one. Syslog takes none. If the settings encryption key is missing or changed, the page
says the stored token cannot be opened.

<!-- field: index -->
## Index or data stream

The Splunk index, or the Elastic data stream (default `logs-openplanner-default`). Letters,
digits and `- _ . : *`.

<!-- field: source -->
## Source or host name

The Splunk source, or the host name in syslog messages. Empty leaves it out.

<!-- field: min_level -->
## Lowest level

Ordinary log lines at this level and above. A destination can only **narrow** what the
platform logs: the lines it can receive are those the log level allows. For trace lines the
log level must be TRACE under **Advanced settings** as well (it falls back by itself after at
most four hours), and the option below must be ticked.

<!-- field: ca_pem -->
## Extra certificate authority

For a platform whose certificate comes from an authority the server does not know, paste the
authority's public certificate (PEM). It is trusted for this destination only. A private key
is refused.

## Which lines a destination receives

- **Audit events** (admin actions, system events, security events): those whose category is
  ticked. The level does not apply, as on standard output.
- **Diagnostic events**: only with the option ticked. They are debug detail written while
  diagnostic mode is on for a group, and leave the installation only to a destination that asks
  for them.
- **Trace lines**: only with the option ticked. Every request (route, not the address), job
  step, outgoing call (host, not the address), slow database statement and page render. No
  personal data.
- **All other lines**: at the lowest level and above.

Personal data is masked in every line, as on standard output.

## Reading the status

Each process, the **web** and the **worker**, has its own queue and reports separately. **Sent**
and **dropped** count lines; **queued** are waiting. **Failed** counts failed deliveries. A
failing destination is retried with a growing wait, up to the maximum under **Advanced
settings**; the first failure and the recovery are in the audit log as system events. A line
the platform refuses for good (it is malformed for that platform) is dropped, so it cannot
block the rest. Reasons: **unreachable** (address, port, firewall, time-out), **refused** (check
the token), **tls** (certificate not trusted), **server** (the platform failed itself),
**not ready** (the stored token cannot be opened).
