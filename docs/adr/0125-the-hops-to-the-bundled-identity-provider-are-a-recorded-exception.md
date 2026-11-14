# 0125. The hops to the bundled identity provider are a recorded exception

Status: accepted, partly built (2026-11-14), found while documenting the application
architecture: a production installation with the installer's default identity provider refuses
to start. Amends ADR 0091 point 2 (two more recorded exceptions) and brings the start-up check in
line with ADR 0112 point 4 and ADR 0106 points 1 and 4. Step 1 is built in the pull request that
adds it; step 2 (a fixed address, point 5) is planned; see *Build status*.

## Context

Since ADR 0106 step 5b the installer bundles Keycloak in every mode, production included,
unless `--idp own` says otherwise. `scripts/render-keycloak-realm.sh` hands web and worker the
login in `secrets/keycloak-app.env`, which Compose reads after `.env`. With an https public
address, which production always has, that file says:

- `OIDC_DISCOVERY_URL=http://idp:8080/idp/realms/openplanner/.well-known/openid-configuration`:
  the compose service `idp` on the internal network, as ADR 0112 point 4 decided ("web still
  talks to Keycloak on the internal network");
- `OIDC_ISSUER=https://<host>/idp/realms/openplanner`: the address browsers use through the
  proxy, pinned as ADR 0097 requires.

The start-up check (`Settings.production_problems`, ADR 0042 §11) refuses every discovery URL
that does not start with `https://` (`oidc_incomplete`), and in production it raises. Web,
worker and the CLI commands the installer runs therefore refuse to start: **a production
installation with the installer's default never comes up**. Nothing caught it. The installer's
tests run against a fake `docker compose` that never starts the image; the installer's own
check reads `.env` and `secrets/config.env`, where the bundled login never is; and test hosts
such as docker01 do not run in production mode, so there the check only listed the login on
System status as something production would refuse.

ADR 0091 says that every hop that carries personal data is encrypted, or a recorded
exception, and point 2 records one: the bundled proxy to web on one host. Two hops to the
bundled provider carry personal data in plain HTTP and are recorded nowhere:

- **web and worker to `idp:8080`** on the compose network: the client secret, the
  authorisation code and the ID token with a staff member's name and e-mail address; the staff
  directory (`KEYCLOAK_ADMIN_URL`, ADR 0095) takes the same hop with the staff list and its own
  client secret;
- **the proxy to `idp:8080`** on the `edge` network (`IDP_UPSTREAM`, ADR 0112): everything a
  browser sends to `https://<host>/idp/`, which is staff passwords, one-time codes and
  Keycloak's cookies, and also the portal's session cookie, because `__Host-op_admin` has
  `Path=/` on the same host name.

**The name `idp` is not pinned.** Docker's own resolver answers `idp` only while the container
is attached to the network. When it is not (stopped, being recreated, or held back by an
unhealthy `idp-db`), Docker passes the name to the host's resolver, which may complete it with
the host's search domains. Whatever answers there receives both hops' traffic:

- *browser traffic at once*, through the proxy's route: passwords, one-time codes and the
  portal's session cookie;
- *the back channel*: because the key set is fetched from the same name, it can sign an ID
  token that carries the pinned issuer (a public string) and any roles, and so log in as a
  superuser; once the cached metadata expires (an hour) it also chooses where browsers are sent
  to log in;
- *both client secrets*, the platform's and the staff directory's. These outlive the outage:
  the render script keeps existing values, so neither a new render nor a restart replaces them.
  The directory's service account may manage users, and the proxy serves Keycloak's token
  endpoint and admin API under `/idp/*`, so that secret alone lets its holder create a
  superuser in the genuine realm afterwards.

Test and lab installations have carried this since ADR 0112; production was spared only because
it did not start. The proxy's route to web (`web:8000`, ADR 0091 point 2) resolves its name in
the same way, for the seconds web is recreated on an update.

Two other ways out were weighed:

- **Render an https discovery URL**, `https://<host>/idp/...`, so web fetches through the
  proxy. The login would then depend on reaching the installation's own public address from
  inside the container (hairpin routing through a load balancer or a split DNS), and on
  trusting the certificate there: an own certificate from an organisation's authority, or the
  internal authority of ADR 0112, is not in web's trust store. A login that works on one
  network and not on the next is worse than the exception, and it reverses ADR 0112 point 4.
  It would also leave the proxy's own hop and the directory as they are.
- **TLS on the internal hops**: Keycloak serving https on the compose network, with a second
  certificate, its authority and its renewal, for hops that are meant never to leave the host.

## Decision

1. **The hops to the bundled identity provider stay plain HTTP on one host, as a recorded
   exception** next to the one of ADR 0091 point 2, for the same reasons: the networks hold
   this installation's containers only, the hops are meant never to leave the host, and whoever
   can read those networks has the host, with the secrets files and the provider's database. It
   covers two hops: web and worker to the provider (the login's discovery document, token
   endpoint and key set, and the staff directory's admin API), and the proxy to the provider
   (the browser's `/idp` route). Point 5 makes "never leaves the host" hold also while the
   container is down.
2. **Keyed on the bundled provider, never on plain http as such.** In production the start-up
   check accepts an http `OIDC_DISCOVERY_URL` only when it is exactly the bundled provider's
   internal address behind the proxy,
   `http://idp:8080/idp/realms/openplanner/.well-known/openid-configuration` (the compose
   service on its fixed port, the `/idp` path ADR 0112 fixes, the realm of the template),
   **and** `OIDC_ISSUER` is set and starts with `https://`. The genuine provider derives the
   issuer and the endpoints it sends browsers to from one host name (`KC_HOSTNAME`), so with an
   https issuer the browser reaches the proxy over https. Every other http discovery URL, and
   this one with an http or a derived issuer, is refused as before. The rule is
   `bundled_idp_hop` in `app/domain/web_transport.py`, beside the proxy hop of ADR 0091.
3. **The pinned issuer stays as ADR 0097 decided.** The discovery document and every ID token
   must carry `OIDC_ISSUER` exactly. Because the exception requires `OIDC_ISSUER`, the issuer
   of the bundled login is never derived from the internal address.
4. **Recorded where the first exception is**: the outbound connections of `docs/dataflow.md`
   (with rows for both hops, and the staff directory, which was missing), `docs/threat-model.md`,
   `docs/asvs.md` (V12.3.3), the architecture, the runbook, and the text of `oidc_incomplete` on
   System status. `oidc-check` already reports the internal back channel as expected (ADR 0112).
5. **The provider is reached at a fixed address, never through a name the host could answer.**
   The bundled provider gets a fixed address on an internal network that web, worker and the
   proxy share, as the proxy has on `edge` (ADR 0091 point 2). The rendered login, the staff
   directory, the exception of point 2 and the proxy's route use that address; the installer
   moves an existing host over and gives it new client secrets, since a host that ran on the
   name may have handed the old ones out; and a check on a real Docker host
   (`scripts/check-proxy.sh`) shows that both hops fail closed while the container is down.
   The proxy's route to web is pinned the same way, so no hop on the host is resolved by name.
6. **Leaving the bundled provider removes it.** With `--idp own` the installer deletes
   `secrets/keycloak-app.env` (Compose reads it after `.env`, so its login and directory would
   otherwise override the ones the operator chose, now that production accepts that login),
   removes `IDP_UPSTREAM` from `proxy.env`, and, once the proxy, web and worker run without
   them, stops and removes the `idp` and `idp-db` containers. The volumes and
   `secrets/keycloak.env` stay, so `--idp bundled` brings the provider back with its accounts.
7. **The host owner is told.** Until step 2 is built, the installer warns when it sets up
   production with the bundled provider, and names this ADR and `--idp own`; the install guide
   says the same.
8. **A recovery for a host that ran on the name.** The runbook says what to do after an
   unexplained outage of the `idp` container on such a host: give both clients new secrets,
   sign everyone out of Keycloak and of the portal, and check the realm's accounts and
   credentials and the portal's audit log from the start of the outage until then, because
   what was taken keeps working after it.

## Settings

None. The internal address is fixed on purpose: the compose file, the proxy route and the
render script agree on it (ADR 0112 fixes `/idp`), and a setting would turn the exception into
any http address the host names. There is no switch either: an installation that does not
want the hops chooses `--idp own`.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #834 | merged |
| 1 | `bundled_idp_hop` in the start-up check; the files a production install writes tested against it (`tests/scripts/test_install.py`), the render script tied to the same address; `--idp own` removes the bundled login, route and containers (point 6); the installer's warning (point 7); the recovery in the runbook (point 8); data flow, threat model, ASVS, architecture, runbook, install guide and the System status text (nl, en) | #834 | merged |
| 2 | A fixed address for the bundled provider, used by the login, the staff directory, the exception and the proxy route, and the proxy's route to web pinned the same way; the installer moves a host over with new client secrets; the real-Docker check (point 5) | | planned |

## Consequences

- A production installation with the installer's default starts. One with `--idp own` keeps
  its own login, also when it ran the bundled provider before, and no longer serves `/idp`.
- A test or lab installation with the bundled provider behind the proxy no longer lists
  `oidc_incomplete` on System status, because production would accept it.
- **Until step 2, production carries the risk in the Context.** While the `idp` container is
  down, whoever answers for that name through the host's resolver receives staff passwords and
  the portal's session cookie, can log in as a superuser, and keeps both client secrets. The
  product owner weighs that against production not starting at all with the default. Step 2
  should therefore be built before a production host goes live on `--idp bundled`; until then
  the advice for such a host is `--idp own`, and the installer's warning says so.
- The admin console under `https://<host>/idp/admin/` and the forwarding of the portal's cookie
  to the provider come from ADR 0112's route; this ADR records them, and closing them is a
  decision of its own.
- `KEYCLOAK_ADMIN_URL` is still not checked at start-up: the staff directory exists only for
  the bundled provider (ADR 0095), and the render script writes its internal address. Step 2
  moves it to the fixed address with the login.
