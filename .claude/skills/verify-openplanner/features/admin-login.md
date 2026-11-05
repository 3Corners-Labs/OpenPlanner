# Admin login through Keycloak

Staff open the admin portal, sign in at the identity provider (here the development Keycloak
realm `openplanner`, standing in for an organisation's own provider; ADR 0013), see what their
role allows, and log out, which ends the session at the provider too. Someone who can sign in but
holds no role is told so and gets no menu.

## Sub-features

- `login-redirect`: an anonymous visit to `/admin/` goes to the sign-in page of realm
  `openplanner` on http://127.0.0.1:8090.
- `login-dashboard`: `groupadmin` lands on "Overzicht", where "Uw groepen" lists only "Demo:
  videogesprek".
- `logout`: the profile menu's "Uitloggen" goes to Keycloak's logout question; after it the
  portal says "U bent uitgelogd".
- `login-again`: after logging out, `/admin/` asks for a sign-in again.
- `login-norole`: `norole` signs in and sees "Geen toegang" with "U heeft geen rol in het
  beheerportaal" and an empty menu.

## How to get to it (user POV)

- http://127.0.0.1:8010/admin/, or any admin address: after signing in, the portal returns to
  its path.
- The e-mail-first page `/admin/login`, when organisation identity providers route logins by
  email domain (ADR 0083; switched off on this stack).
- The profile menu at the top right (the user's initials and name) for logging out.

## Driving it with journey.mjs

Preconditions:

- `stack.sh doctor` shows `login` as PASS (issuer and discovery agree).
- The development users of `dev/keycloak/README.md`; the journey reads their password from
  `dev/keycloak/openplanner-realm.json`.

- **Visit anonymously.** `page.goto('/admin/')`. The address starts with
  `http://127.0.0.1:8090/realms/openplanner/`. Evidence `admin-login/01-keycloak-sign-in`.
- **Sign in.** Fill `#username` with `groupadmin` and `#password`, press Enter. Back on
  `http://127.0.0.1:8010/admin…`; heading `Overzicht`; list `Uw groepen` has one item,
  containing `Demo: videogesprek`. `02-dashboard`.
- **Log out.** Click `details.admin-profile > summary`, then button `Uitloggen`. Keycloak's
  `/logout` page; click `#kc-logout`. Heading `U bent uitgelogd`. `03-logged-out`.
- **Check the session ended.** `page.goto('/admin/')` goes to the Keycloak sign-in again.
  `04-sign-in-again`.
- **Sign in without a role.** Sign in as `norole`. Heading `Geen toegang`, the text `U heeft
  geen rol in het beheerportaal`, and navigation `Beheermenu` holds no links; the answer is a
  403, recorded as an expected console error. `05-no-role-refused`.
- **Proof.** The `admin-login/` folder; `logs/app.log` (after `down`) holds the `admin.login`,
  `admin.logout` and `oidc.no_role` events.

Command: `node .claude/skills/verify-openplanner/scripts/journey.mjs admin-login`

## Gotchas

- Keycloak redirects only to the addresses in its realm. `stack.sh up` imports copies of the
  development realms with this stack's web port; a Keycloak started another way knows only port
  8000 and stops at *Sign in* with an invalid `redirect_uri`.
- The browser must reach Keycloak at the address it announces (`KC_HOSTNAME`), and web compares
  that issuer with `OIDC_ISSUER`; doctor's `login` line checks both before you drive.
- After signing in the portal keeps the path, not the query string: open a page with `?day=` or
  `?lang=` again after the login.
- There is no second factor on this stack (`ADMIN_MFA_REQUIRED=false` in `.env.example`); the
  bundled identity provider of a test or lab installation requires one (ADR 0100, ADR 0106).
  This journey does not prove that part.
- The first login of a user adds them as staff (`staff.created` in the log); that is expected on
  a fresh database.
- The password is a development-only value that production refuses. It is never typed into chat
  or written to evidence; screenshots are taken before the fields are filled.
