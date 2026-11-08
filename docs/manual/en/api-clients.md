# API clients

Contact-center bots, such as the Webex AI Agent, use the machine API to look up free times.
On this screen the superuser decides which bots may connect and what each may reach. Open it
with **API clients** in the menu. Only the superuser sees it. A bot never sees personal data.
How a bot connects is described in `docs/agent-api.md`.

## Switches

- **API on** is the emergency stop. Switched off, no tokens are issued and existing tokens
  (valid for at most 10 minutes) are refused at once, for every bot.
- **Publish documentation** makes the OpenAPI specification and the documentation page
  reachable. The address is shown next to the switch. While it is off they cannot be reached.

When the API is not enabled in the environment, the screen says so. You can prepare clients
already; they work once the server operator enables the API.

## The list

Every client has a row with its name and client ID, its organisation and teams, its scopes,
whether it is on, until when its secret is valid and when it last asked for a token. **Expires
soon** appears when the secret ends within 30 days. A name leads to the client's page.

Above the table you search by name or client ID and, with clients of several organisations,
pick an organisation. The count says how many clients match and how many there are in all.
The search text does not come back onto the screen; the page only says that a search is
active.

## A new client

Choose **New API client**.

<!-- field: name -->
### Name

For yourself: for example the name of the bot in Webex. At most 80 characters.

<!-- field: organisation -->
### Organisation

A client belongs to one organisation. This cannot be changed later.

<!-- field: lifetime_days -->
### Secret lifetime

How long the client secret is valid: 90, 180 (default) or 365 days. Create a new secret
before it ends; after that the API issues no tokens to this client.

<!-- field: scopes -->
### Scopes

What the client may do. At least one is needed:

- **Read free times** (`availability:read`): look up free times of groups.
- **Export and import the configuration package (whole installation)**
  (`configuration:transfer`): acts on the installation as a whole, whatever the client's
  organisation.

<!-- field: groups -->
### Teams

Optional. Tick the teams the client may reach. Nothing ticked means every team of the
organisation.

<!-- field: networks -->
### Allowed networks

Optional. One IP address or network per line, for example the outgoing addresses of your WxCC
region. Empty means any address.

With **Create client** the client is registered. The next page shows the **client ID**, the
**client secret**, its expiry and the **token URL**. You see the secret only now: copy it to
the contact center's vault. OpenPlanner keeps only a hash. Never put the secret in an email,
chat or ticket. Lost it? Create a new secret.

## One client

The client's page shows its organisation, whether it is on, until when its secret is valid
and when it last asked for a token.

- **Switch off** / **Switch on**: a client that is off gets no tokens. Switching off revokes
  all its tokens at once.
- **Revoke tokens**: revokes every live token; the bot then requests a new one.
- **Create new secret**: choose a lifetime, then you see the new secret once. The old secret
  and all tokens stop working at once.
- **What the client may reach**: change name, scopes, teams and networks and save. A change
  of scopes, teams or networks revokes the client's tokens. The organisation stays as it is.

When the client's access names a team that has been archived, the page says so. As long as the
link stays, the team is not erased. Save the form or delete the client to let the link lapse.

## Deleting a client

A client can only be deleted when it is switched off; the button is not offered until then.
Deleting erases the client with its tokens and team links. The audit log keeps its ID.
