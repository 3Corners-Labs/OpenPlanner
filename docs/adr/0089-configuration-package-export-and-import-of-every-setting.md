# 0089. A configuration package: export and import of every setting, for a fast new installation

Status: proposed (2026-11-01), requested by the product owner. Extends ADR 0008 (secrets at
rest), ADR 0042 (settings classes) and ADR 0050 (host installer). Partly built; see *Build
status*.

## Context

OpenPlanner is open source since ADR 0054: anyone can clone it and run `scripts/install.sh`.
A new installation then starts empty. Everything that makes it an installation of *this*
organisation is entered by hand: the organisations and groups with their opening hours,
reminders, texts and form fields; the platform settings; the SMTP server and SMS gateway
per organisation; the Webex Service App and its rotated tokens; the identity providers per
organisation with their client secrets or signing keys; the API clients of the contact-center
bots; the holiday calendars; the branding. The product owner stated the cost on 2026-11-01:
setting up a test installation means re-entering every secret, token and password, and a
secret that was not written down has to be generated and registered again at the provider.

What exists today, and why it is not enough:

- `python -m app.cli backup-db` and `restore-db` (ADR 0029) copy the *whole* database,
  bookings and staff included. A backup holds personal data and is never meant to leave the
  data volume; and the secrets in it are sealed under the source installation's
  `SETTINGS_ENCRYPTION_KEY` (ADR 0008), which a new installation does not have, and must not
  have: `init` refuses to reuse a key.
- `seed-demo` makes the development organisations, nothing of a real one.
- The class A and B variables are a host matter (ADR 0042 §1); the installer asks for them.
  Since ADR 0042 step 6, every integration secret of the platform lives in the portal (class
  C, sealed in `integration_credentials`); the environment holds only the bootstrap keys, the
  login (`OIDC_*`), the host's addresses and the kill switches.

So the configuration of an installation is almost entirely in the database, in tables that
hold no participant data, next to tables that do. The missing piece is a *portable* copy of
the first kind of table, with its secrets readable on another installation by someone who
knows a passphrase, and never by someone who only holds the file.

The product owner also asked (2026-11-01) that the same is possible through the API, so an
installation can be provisioned by a pipeline, not only by a person.

## Decision

1. **A configuration package is a SQLite file.** `openplanner-configuration-<installation>-
   <date>.sqlite` is a copy of the database made with SQLite's online backup API, from which
   every *state* table has been emptied and that has then been vacuumed, so the removed rows
   are not in its free pages. It keeps `alembic_version`, so the package carries the schema
   revision it was made with. No new format, no mapping of tables to JSON that must be kept
   up to date by hand: a new table is configuration or state by its name in one registry, and
   a test fails when a table is in neither.
2. **One registry says what is configuration.** `app/domain/configuration_package.py`
   classifies every table: *configuration* (organisations, groups, schedule rules and
   overrides, reminder rules, texts, form fields, contacts, holiday calendars and their
   assignments, closure scenarios and images, branding, channel settings, platform and
   organisation settings, integration credentials, identity providers and their keys, API
   clients and their groups, custom roles, the API switch, `settings_version`) and *state*
   (bookings, slots, jobs, booking events and call status, audit events, staff and their
   memberships and refusals, active closures and their notices, shortfall acceptances, API
   access tokens, webhook events, integration reports, diagnostic modes, maintenance state,
   update requests, release checks and listing, running versions, certificate checks and the
   own certificate). The PII fields of CLAUDE.md are all on `bookings`, a state table; the
   configuration tables name staff only as a video host address of a group.
3. **Secrets travel sealed under a passphrase, never under the installation's key.** The
   four sealed columns (`integration_credentials`, `channel_settings`, `identity_providers`,
   `identity_provider_keys`) are opened with the installation's `SecretBox` and sealed again,
   under the same record names, with a box whose key is derived from the passphrase with
   scrypt (N 2¹⁶, r 8, p 1, a 16-byte random salt stored in the package). The package carries
   a verifier (a fixed text sealed under the package key), so a wrong passphrase is told
   from a damaged file. The passphrase is at least 16 characters; it is never stored or
   logged. A package without the passphrase holds no secret in clear text; a package with
   the passphrase holds every secret of the installation, and the manual says so.
4. **The login goes with it.** The package also carries the portable environment values
   `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `OIDC_SCOPES`, sealed
   like the secrets. They are the only class A values a new installation needs that are not
   tied to the host, and without them nobody can log in to the new installation to import
   anything. The bootstrap keys (`SETTINGS_ENCRYPTION_KEY`, `MANAGEMENT_TOKEN_KEY`,
   `SESSION_SECRET`) never travel: a key per installation is the point of ADR 0042 §9. The
   host's addresses (`PUBLIC_BASE_URL`, `TLS_*`, `FORWARDED_ALLOW_IPS`), `INSTALLATION_NAME`,
   `ENVIRONMENT` and the kill switches stay the host's, and so do the class C and D locks in
   the environment: a lock is a host policy, not configuration.
5. **Import only into an installation that is not in use.** An import replaces every
   configuration table in one write transaction (`BEGIN IMMEDIATE`, foreign keys deferred and
   checked before the commit) and is refused, with `configuration_installation_in_use`, when
   the installation has any booking, slot or active closure. That is the "new install" the
   product owner asked for; it keeps the import from ever cutting a booking loose from its
   group or an active closure from its scenario. Staff records, the audit log and the
   installation's own state stay as they are. The package's schema revision must equal the
   running one (`configuration_package_version`); migrating an older package forward is step
   5. After an import `settings_version` moves on, so web and worker reload the settings
   within ten seconds; text overrides follow within their cache bound and each identity
   provider adapter is rebuilt on its first use.
6. **Who, and where.** Export and import are the superuser's, under
   `settings.platform.manage` with a recent login (both read or replace every secret of the
   platform), on the *Advanced settings* screen (platform → *Advanced settings*, a block
   *Configuration package*; ADR 0047: a new concern of the platform's configuration, on the
   screen that already shows that configuration; no new screen). Both are audited as
   `settings.platform.manage` with the detail `configuration.export` or
   `configuration.import,organisations:N,groups:N,…`, never with a passphrase or a value. On
   the host the same runs as `python -m app.cli config export|show|import|env`, actor
   `system:cli`; the passphrase comes from a file (`--passphrase-file`), never from the
   command line, where it would be in the shell history and the process list.
7. **The same through the machine API (step 3).** An API client (ADR 0023) may hold the
   scope `configuration:transfer`; `GET /api/agent/v1/configuration` returns the package and
   `PUT` imports one, the passphrase in a header, with the same rules and audit as the
   portal. A client with that scope acts on the whole installation, whatever organisation
   registered it; only the superuser registers clients, and the manual says what the scope
   means. The agent API's own switch (`AGENT_API_ENABLED`, class B) and its rate limits apply.
8. **The installer takes a package (step 4).** `scripts/install.sh --configuration FILE
   --passphrase-file PATH` writes the login values of point 4 to `secrets/config.env`, which
   `docker-compose.yml` reads next to `secrets/secrets.env`, does not ask for what the
   package answers, and imports the package once the web container is healthy. A new
   installation is then usable, with every organisation, group, channel, provider and
   integration of the one it was exported from, after one command.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `configuration_package_max_mb` | C platform | 64 | 1–512 | `settings.platform.manage` | the largest package the portal and the API accept for import; branding fonts and closure images make up most of a package |

**Fixed on purpose:** the passphrase length (16, a protection; never lowered from the
portal), the scrypt parameters and the salt size (crypto parameters, ADR 0008), the package
format version (1) and the record names the secrets are sealed under (they bind a ciphertext
to its record). The classification of tables is code, checked by a test, not a setting.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #298 | in PR |
| 1 | The package: table registry, export and import adapter, `config export\|show\|import\|env` on the host, the setting | #298 | in PR |
| 2 | The block *Configuration package* on *Advanced settings*: export with a passphrase, import with a passphrase and a confirmation; manual topic | #298 | in PR |
| 3 | Machine API: scope `configuration:transfer`, `GET`/`PUT /api/agent/v1/configuration`, `docs/agent-api.md` | | planned |
| 4 | Installer: `--configuration FILE --passphrase-file PATH`, `secrets/config.env` in `docker-compose.yml`, `docs/install.md` | | planned |
| 5 | A package of an older schema revision is migrated forward on import | | planned |

## Consequences

- `docs/threat-model.md` gets the flow *configuration package*; `docs/dataflow.md` the
  diagram; `docs/runbook.md` the host commands next to backup and restore; the manual topic
  *advanced-settings* the block. ADR 0050's install page gets the package in step 4.
- A package is a complete set of the platform's secrets. The export is audited, needs a
  recent login, and the manual tells the superuser to treat the file and the passphrase as
  what they are. There is no "export without secrets": a package without them would still
  need every secret re-entered, which is the cost this ADR removes.
- The restore of ADR 0029 is unchanged: a backup is for *this* installation, a package is
  for *another*.
- No new dependency: scrypt and AES-GCM come from `cryptography`, SQLite's backup API from
  the standard library.
- A later ADR that adds a table must put it in the registry; the test says so at once.
- The port `ConfigurationTransfer` is database-independent; the adapter is SQLite's (the
  package *is* a SQLite file, as the database is). Postgres is not a target (CLAUDE.md,
  2026-10-23); an ADR that changes that gives the port a second adapter that writes and reads
  the same package file, and the portal, the CLI and the API stay as they are.
