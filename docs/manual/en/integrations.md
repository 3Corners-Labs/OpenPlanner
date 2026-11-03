# Integrations

This screen shows the state of the connections with Webex (video meetings) and Webex Contact
Center (WxCC) for the whole installation. Open it with **Integrations** in the menu. Only the
superuser sees it. Secrets are stored encrypted and are never shown.

Most of the screen is a report. The only button on it is **Check the Service App**. The other
checks are refreshed on the command line of the web container; the screen then shows their
last result.

## Webex Service App

The card shows whether the Service App is connected, until when the access token and the
refresh token are valid, and whether Webex Contact Center is connected. *Not used yet* means
no token has been requested so far.

When the refresh token expires soon, a warning at the top of the screen gives the date. Create
a new token before that date, as the runbook describes; otherwise meetings and work items can
no longer be created.

## Channels

A table with one row per organisation shows whether email and SMS are **Configured**, **Secret
missing** or **Not configured**. The organisation's name leads to its **Channels** screen,
where you change them (see [Channels](channels.md)).

## Check the Service App

<!-- field: service_app_check -->
### Check the Service App button

The button makes every kind of call OpenPlanner makes to Webex and WxCC, without changing
anything: WxCC refuses an empty task and an unknown task id, and that is the expected answer.
Per call the table shows the HTTP status and a verdict:

- **OK**: the call succeeds.
- **Failed**: a required call fails. Read the explanation; on a 401 or 403 it names the scope
  that is probably missing. See the runbook (Service App).
- **Warning**: a call that is not required fails.

The result stays on the screen with the time of the last check. The check is also recorded in
the audit log as passed or failed. While maintenance mode is on you can still use it, to test
a repaired integration. Without a check yet the screen shows the command line alternative
(`python -m app.cli wxcc-check`).

## Webhooks

The two addresses that receive the events of Webex and WxCC, each with whether a secret is
set. An address without a secret is disabled. Register the addresses at the platforms with
`python -m app.cli register-webhooks`.

## Registrations at Webex and WxCC

A list of every webhook and subscription at the platforms that points at OpenPlanner, with its
state at the last check:

- **Current**: in order.
- **Stale**: still there but no longer right. The reason is under the state: another address
  (an old tunnel or base URL), no host filter, a host account no group uses, an event
  OpenPlanner does not use, inactive, or a duplicate.
- **Not OpenPlanner's**: belongs to another application and is left alone.

Under **Missing** are registrations that should exist but do not. The list is not refreshed by
this screen: run `python -m app.cli webhooks-list`. Clean up stale registrations with
`python -m app.cli webhooks-reset` (runbook, section 6).

## Delivery self-test

Signed test events sent through the webhook address to OpenPlanner itself, with unknown ids so
no appointment changes. Per address and test the result says whether it was delivered and
verified, refused as it should be (a wrong signature), or what went wrong: a signature that
differs from the web container's secret, verification that is switched off, an address that
does not reach OpenPlanner, a tunnel or proxy error, or an address that cannot be reached.
Refresh it with `python -m app.cli webhooks-health`.

## Groups

A table of every group with whether video is on and whether work items are sent to WxCC (with
the routing value). The group's name leads to its settings, where you change this.
