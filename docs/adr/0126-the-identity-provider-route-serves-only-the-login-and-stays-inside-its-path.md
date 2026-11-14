# 0126. The identity provider's route serves only the login and stays inside its path

Status: proposed (2026-11-14), found by the security review of PR #834 (ADR 0125). Amends ADR
0112 point 4 (the `/idp` route passed every path, every cookie and every answer) and restores
what ADR 0090 point 3 and ADR 0106 point 4 promised: the provider's admin console on the host
only. Partly built: steps 0 and 1 merged (#845); step 2 open.

## Context

ADR 0112 point 4 serves the bundled Keycloak under `/idp` on the portal's own host name and
certificate: `handle /idp/*` with `reverse_proxy idp:8080`, for every path, with every header
in both directions. Measured on Keycloak 26.8.1 behind the bundled proxy (2026-11-14):

1. **The admin console is public.** `https://<host>/idp/admin/master/console/` and realm
   `master` answer 200 through the proxy, and the admin REST API answers 401 (reachable).
   Realm `master` holds `admin`, who rules every realm, with a password and no second factor.
   The runbook (*Bundled identity provider*), the threat model (the bundled provider's row),
   ADR 0090 point 3 and ADR 0106 point 4 all say the console is on the host's loopback only;
   the runbook's own `/idp` section documents the public console. Keycloak's guide
   *Configuring a reverse proxy* lists `/admin/` and `/realms/master/` as "only internally"
   and `/realms/` and `/resources/` as the paths to expose.
2. **The portal's cookies reach the provider.** On https every cookie of the portal has the
   `__Host-` prefix and therefore `Path=/` (`app/web/cookies.py`): the admin session
   `__Host-op_admin` (ADR 0098), `__Host-op_link`, `__Host-op_req`, `__Host-op_display`, and
   `lang` (`app/web/locale.py`). The browser sends all of them to `/idp/*`, and the proxy
   handed them to Keycloak, which has no use for them. A session cookie is a credential.
3. **The provider's answers act on the portal's origin.** A path is no boundary in a browser:
   a page under `/idp` is the same origin as `/admin`. The portal's guard against forged
   requests is Fetch Metadata without a token (`app/web/protection.py`), and a request from a
   page under `/idp` is `same-origin`. Measured in Chromium, Firefox and WebKit: a script on
   Keycloak's login page reads a portal page with the admin's cookies (`fetch`) and posts a
   form to `/admin/…`. An answer can also set what a browser applies to the whole origin or
   host: a cookie with `Path=/` (a new `__Host-op_admin` replaces the portal's session), HSTS
   `max-age=0`, `Service-Worker-Allowed: /`, `Clear-Site-Data`, `NEL` and `Report-To` (reports
   about the portal's own requests, whose paths carry management tokens, to an address of the
   answer's choosing), `Alt-Svc`, `Accept-CH`.
4. **What Keycloak offers for the console.** `KC_HOSTNAME_ADMIN` does not do it: Keycloak's
   hostname guide says it does not prevent access to the admin REST API through the frontend
   address, and measured on 26.8.1 the login form of realm `master` still posts to the
   frontend address (`https://<host>/idp/realms/master/login-actions/…`), so a console on the
   admin address cannot log in once the frontend refuses realm `master`. A realm's own
   frontend URL (the realm attribute `frontendUrl`) does: with it on realm `master` the
   console served on the loopback names only the loopback for its code, its login and its API,
   and realm `openplanner` keeps its public address and issuer.
5. **What must keep working.** The login back channel and the staff directory (ADR 0095) call
   `http://idp:8080/idp/...` on the internal network, never through the proxy; `kcadm.sh`
   runs inside the container. The account console stays public: ADR 0100 point 7 sends staff
   there to change their password and second factor.

## Decision

1. **Only the login is public under `/idp`.** The proxy passes `/idp/realms/openplanner/*`
   (the login, its OIDC endpoints and the account console) and `/idp/resources/*` (their
   assets) to the provider and answers 404 itself for every other path under `/idp`: the
   admin console and its REST API (`/idp/admin`), realm `master`, the welcome page, metrics
   and health. `deploy/proxy/idp.caddy` holds the route; `entrypoint.sh` imports it when
   `IDP_UPSTREAM` is set. `/.well-known/` (RFC 8414) is not passed: no client of the
   installation reads it; the portal reads the realm's OIDC document on the internal network.
2. **The admin console is on the host's loopback.** Realm `master`'s frontend URL is the
   loopback address the compose service publishes, `http://127.0.0.1:8080/idp`. The installer
   sets it after the start, with the realm's mail server (ADR 0112 point 6), when the provider
   is behind the proxy; it refuses to point it at a port published beyond the loopback and
   says so. On a host set up by hand one command in the runbook sets it. Both go through
   `deploy/keycloak/kcadm-as-admin.sh`, mounted read-only into the container: the one place that
   logs `kcadm.sh` in as `admin`, with the container's own password, so no secret is an
   argument on the host. The console is opened on the host, or from elsewhere through an SSH
   tunnel to that port; `kcadm.sh` inside the container works with or without it.
   `KC_HOSTNAME_ADMIN` is not used (context point 4).
3. **None of the portal's cookies reaches the provider.** On the route the proxy removes
   from the `Cookie` header every `__Host-` cookie (every cookie of the portal on https; never
   one of the provider's, whose path is under `/idp/`) and `lang`.
4. **An answer from the provider acts on `/idp` only.** On the route the proxy:
   - confines every cookie the provider sets to `/idp/` and this host: a `Path` outside
     `/idp/` becomes `/idp/` (the browser then refuses a `__Host-` name, so the portal's
     session cannot be replaced) and `Domain` is dropped;
   - drops `Service-Worker-Allowed`, `Clear-Site-Data`, `Alt-Svc`, `NEL`, `Report-To`,
     `Reporting-Endpoints`, `Accept-CH` and `Critical-CH`, and sets HSTS to the portal's own
     value (`app/api/security_headers.py`);
   - sets `Cross-Origin-Opener-Policy: same-origin-allow-popups`, never the portal's
     `same-origin`, so a portal window opened from a provider page lands in a browsing context
     group of its own and the page cannot reach into it;
   - adds a policy next to the provider's own `Content-Security-Policy` (a browser enforces
     both): `connect-src` and `form-action` only under `/idp/`, and `worker-src 'none'`. A
     browser ignores the path after a redirect, so the login's return to `/admin/callback`
     stays allowed;
   - refuses with 403, before the provider sees it, a request that fetches a service worker
     script (`Service-Worker` request header).
5. **One origin stays (ADR 0112).** A host name of its own for the provider would give it its
   own origin, and the portal's Fetch Metadata check would then refuse its requests without
   any of point 4. Not chosen now: it needs a second name and certificate on every
   installation, and a lab without public DNS has one name. Points 1 to 4 close what was
   found; what remains is in *Consequences*. Revisit when a production installation runs the
   bundled provider (ADR 0125 step 2).
6. **Proven against the proxy and against Keycloak.** `scripts/check-proxy.sh` pass 3 puts a
   probe behind the route that sends every header of point 4 and reports what reached it;
   pass 4 runs the Keycloak image pinned in `docker-compose.yml` behind the proxy: the login
   page, every asset it names and the account console load, the console and realm `master`
   answer 404 through the proxy and 200 on the loopback, the console on the loopback names
   only the loopback, and the staff directory's calls on the internal network work. A
   Keycloak update (Renovate) runs the same pass.

## Settings

None, after checking. Every value below is a fixed security rule or follows from the layout:

**Fixed on purpose:**

- The public paths (`/idp/realms/openplanner/*`, `/idp/resources/*`): the realm name is fixed
  by the template (ADR 0090), and a setting would let a host publish the console again.
- The removed cookies (`__Host-*`, `lang`), the dropped headers, the HSTS value, the
  `Cross-Origin-Opener-Policy` value and the added policy: security rules; a test ties the
  HSTS value and the cookie names to the application's own.
- The console's loopback address: the port the compose service `idp` publishes on
  `127.0.0.1`, read by the installer with `docker compose port`.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #845 | merged |
| 1 | `deploy/proxy/idp.caddy` (public paths, cookies, answers, service worker) imported by the entrypoint; `deploy/keycloak/kcadm-as-admin.sh` mounted into the container; the installer sets realm `master`'s frontend URL to the loopback (`idp_console`); `scripts/check-proxy.sh` pass 3 (probe) and pass 4 (Keycloak); runbook, threat model and data flow corrected; ADR 0112's Status line | #845 | merged |
| 2 | docker01 takes the release with step 1: realm `master`'s frontend URL set with the runbook's command, `idp` published on the loopback only (no port 8780); checked from outside (`/idp/admin/` 404, the login works) and through the tunnel (the console) | | planned |

## Consequences

- The console is not reachable through the proxy any more. Operators open it on the host or
  through an SSH tunnel (runbook). On a host that predates step 1, the console on the loopback
  cannot log in until the command of point 2 has run; `kcadm.sh` works throughout.
- Keycloak's pages run under the added policy. A later Keycloak that fetches outside `/idp/`
  or starts a worker would be blocked there; pass 4 shows a broken login page at the Renovate
  pull request, and the experiment of 2026-11-14 (login with a second factor and the account
  console in three engines, no violation) is the record for 26.8.1.
- What remains with one origin: a script on a provider page can still make the browser load a
  portal address by navigation or as an image, so a `GET` that has an effect (an audited
  download) runs in the admin's name, without the script reading the result; the
  provider's own scripts are not restricted (its themes use inline scripts); the provider can
  show any page under `/idp`. A host name of its own (point 5) removes all three.
- A reverse proxy of the operator's own in front of the bundled provider must do the same; the
  runbook lists what.
- Updated with this decision: `docs/runbook.md`, `docs/threat-model.md`, `docs/dataflow.md`,
  ADR 0112's Status line and its row in `docs/adr-status.md`, which still called step 6 open.
