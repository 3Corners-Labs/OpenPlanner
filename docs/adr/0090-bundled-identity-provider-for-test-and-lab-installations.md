# 0090. A bundled identity provider for test and lab installations

Status: proposed (2026-11-01), requested by the product owner. Extends ADR 0013 (OIDC admin
login), ADR 0050 (host installer) and ADR 0089 (configuration package). Not built; see
*Build status*.

## Context

The product owner assumed on 2026-11-01 that Keycloak is part of every installation and that
the superuser can always log in. That is true of a development checkout and of a `lab`
installation only: `docker compose --profile dev` starts Keycloak from `dev/keycloak/` with a
realm whose every password is `dev-only-password`, whose client secret starts with
`dev-only-` (refused in production by the start-up check) and whose admin console is
`admin`/`admin`. A `test` or `production` installation asks for the discovery URL and client
secret of an identity provider of the organisation's own (PingFederate for the first client,
ADR 0013) and has no login until that provider is configured.

For a test installation that is a hurdle, and the reason the product owner's own test host
was installed as a lab. A test installation that can be set up from a configuration package
(ADR 0089) and logged into at once, without an identity provider of the organisation's own,
makes "install, import, try" one afternoon's work. ADR 0042 §8 still stands: no local
account, no break-glass login and no role switcher in OpenPlanner itself; a bundled provider
is an identity provider like any other, reached through the same OIDC flow.

## Decision

1. **Modes `test` and `lab` bundle Keycloak by default.** The installer starts it from the
   compose profile `idp` (today's `dev` profile keeps Mailpit and the development realm) and
   points `OIDC_DISCOVERY_URL` at it. `production` does not, unless `--idp bundled` says so;
   the installer then warns that the organisation's staff then lives in this Keycloak and
   must be managed there.
2. **No development secret leaves the repository.** The installer generates the Keycloak
   admin password, the client secret of `openplanner-admin` and the initial password of one
   user `super` (every built-in role, as the development realm's `super`), writes them to
   `secrets/keycloak.env` (mode 600, next to `secrets.env`), and renders the realm from a
   template in `deploy/keycloak/` with those values. The user `super` must change the
   password at the first login (Keycloak's required action). The installer prints where the
   file is, never a value.
3. **The superuser can always log in.** The bundled Keycloak is on the same host and needs
   no outbound connection; the ordinary login of ADR 0013 applies. Losing the `super`
   password is a Keycloak matter (its admin console, on the host only), not a backdoor in
   OpenPlanner: the CLI of ADR 0042 §10 still creates no user and starts no session.
4. **Organisations keep their own providers.** ADR 0083 is unchanged: an organisation
   registers its own identity provider in the portal; the bundled Keycloak is the platform's
   provider, for the superuser and for organisations that have no provider of their own.
5. **The package carries the login.** The configuration package (ADR 0089 point 4) carries
   the bundled provider's discovery URL, client id and secret; a package made on one bundled
   installation logs in on another bundled installation once Keycloak there holds the same
   client secret (the installer's `--configuration` writes it to `secrets/keycloak.env`).

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | A bootstrap | the bundled Keycloak in `test` and `lab` | | operator | unchanged variables (ADR 0013, 0042); the installer fills them |

**Fixed on purpose:** the Keycloak version is pinned in `docker-compose.yml` and tracked by
Renovate (ADR 0051); the realm template is code.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #298 | in PR |
| 1 | Compose profile `idp`, realm template in `deploy/keycloak/`, `secrets/keycloak.env` | | planned |
| 2 | Installer: default for `test` and `lab`, `--idp bundled\|own` for every mode, `docs/install.md` | | planned |
| 3 | `docs/runbook.md` (managing staff in the bundled Keycloak, resetting `super`), e2e against the template realm | | planned |

## Consequences

- A test installation is one command away from a working login; the development realm stays
  for development and CI only.
- The threat model gets the bundled provider: its admin console is bound to the host's
  loopback, its data volume holds staff names and passwords (the DPIA input in
  `docs/dataflow.md`), its version is a Renovate pin.
- Production stays with the organisation's provider by default; the first client's
  PingFederate setup of the runbook is unchanged.
