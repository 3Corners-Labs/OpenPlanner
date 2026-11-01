# 0098. Admin sessions held on the server

Status: proposed (2026-11-01); partly built: step 1 (register, logout revokes, rotation, idle and absolute check) is merged (#350), steps 2 to 4 are planned. Requested by the product owner after the ASVS 5.0.0 L2 audit of
1 November 2026 (V7, V3.3). Supersedes ADR 0013 §5 (Session); the rest of ADR 0013 stands.
See *Build status*. Merged in #337 on 2026-11-01 without `po:approved`
before the merge (the label followed seven minutes later); awaiting the product owner's
acceptance. Amended on 2026-11-01 (re-evaluation): this ADR is the single home for ending admin
sessions; ADR 0046 §8 and §10 point here. §9b uses ADR 0046's `staff.manage` at organisation
level instead of a new permission. The algorithm and the rotation of `SESSION_SECRET`, which
ADR 0094 §7 defers to this ADR, are not decided here yet (point 9 keeps rotation as the last resort);
a later step records them. Step 4 (`Secure` on every https installation) may come before steps
2 and 3: CLAUDE.md requires it today.

## Context

The admin session *is* the signed cookie (`op_admin`, ADR 0013 §5). There is nothing on the
server to revoke, so:

- logging out only clears the browser's copy; a cookie copied earlier stays valid until its 8 h
  absolute end (V7.4.1);
- a new login does not invalidate the old cookie (V7.2.4);
- there is no idle timeout (V7.3.1), no policy for parallel sessions (V7.1.2), and no way for a
  person or an administrator to end a session (V7.4.5, V7.5.2). The only lever is rotating
  `SESSION_SECRET`, which ends everyone's session;
- after switching a person off at the identity provider they keep reading for up to 8 h (V7.4.2);
- ADR 0013 §5 describes only the absolute lifetime and says the cookie holds "subject, display
  name and scope only". The code has since put claims, entitlements and preferences in it (V7.1.1,
  V7.1.3, V7.6.1);
- the cookie is `op_admin`, `Path=/admin`, and `Secure` only when `ENVIRONMENT=prod`, so a
  `test` installation on HTTPS (ADR 0064) sends it without `Secure` (V3.3.1, V3.3.3).

The cookie also carries things that exist before a session does (the OIDC login in progress:
`state`, `nonce`, PKCE verifier, provider; the test login; the update snooze; UI preferences).
Those stay in a signed cookie. Replacing the cookie transport buys nothing; the fix is a
server-side record of the session.

Recorded choices of the product owner (2026-11-01): idle timeout default **60 minutes**
(planners work in the portal all day; without JavaScript there is no warning before it fires),
at most **5 sessions per person**, the oldest lapses.

## Decision

1. **A register of sessions.** Table `admin_sessions`: `id_hash` (`sha256` of 32 random bytes;
   the identifier itself is never stored), `subject` (provider-qualified), `provider`, the
   claims as JSON (today's `LoginClaims`), `login_at`, `last_seen_at`, `ended_at`, `end_reason`
   (`logout`, `idle`, `expired`, `replaced`, `limit`, `ended_by_self`, `ended_by_admin`,
   `restore`, `secret_rotated`, `cli`). The row is the session. Claims live on the server.
   These are staff data, so the participant PII list in CLAUDE.md does not change, but the
   table goes into `docs/dataflow.md`.
2. **The cookie holds a reference only.** The signed cookie carries `sid` (the 32 random bytes,
   encoded) next to what it already holds before login (pending login, test login, snooze,
   preferences). Name, organisations, roles and entitlements leave the cookie. The identifier
   is never logged, nor put in an audit event, a URL or a job payload.
3. **Every request looks the session up.** `current_login` finds the row by `sha256(sid)` and
   accepts it only when `ended_at` is empty, `now - login_at <= admin_session_hours` and
   `now - last_seen_at <= admin_idle_minutes`. Otherwise the session is ended with the matching
   reason (`expired` or `idle`) and an audit event, and the person is sent to the login.
4. **Idle.** `last_seen_at` is written at most every `staff_last_seen_interval_seconds` (ADR
   0046, existing setting, 60-3600, default 300). The effective idle limit therefore lies
   between `admin_idle_minutes` and `admin_idle_minutes` plus that interval. No second
   interval setting is added.
5. **Absolute lifetime and re-authentication** are unchanged: `admin_session_hours` (8) and
   `admin_reauth_minutes` (60; changes need a recent login). Re-authentication creates a new
   session like any login (point 6).
6. **Rotation at login.** Every login, re-authentication included, creates a new row and a new
   `sid`. The row of the `sid` the browser brought with it is ended with `replaced`. A copied
   old cookie is therefore dead after the next login.
7. **Logout revokes first.** Logout sets `ended_at` (`logout`), then clears the cookie, then
   follows the existing provider logout (ADR 0013 §7). A cookie copied before logout no longer
   opens `/admin`.
8. **Parallel sessions.** Allowed (tabs share one cookie; devices count separately), at most
   `admin_max_sessions_per_person`. At the login that exceeds it, the session with the oldest
   `last_seen_at` is ended with `limit`. A login is never refused: that would lock out someone
   who left a laptop open.
9. **Ending sessions.**
   - (a) A person sees their own sessions (login time, last active, current one marked; no
     identifier, no IP or device data) and may end the others. This needs no permission
     because it concerns only their own sessions.
   - (b) An administrator ends all sessions of a person from that person's record in the staff
     overview (ADR 0046), with a new permission in the matrix at **organisation level**. A
     session belongs to a person, not to an organisation, so ending it also ends what that
     person uses for other organisations. This is a deliberate choice: revoking is a
     protective act and cannot widen anyone's rights. The permission only applies to persons
     the administrator's scope already shows in the staff overview. Its final name is fixed in
     step 3 together with the matrix.
   - (c) All sessions: `python -m app.cli sessions end-all --reason ...` on the host.
     Rotating `SESSION_SECRET` keeps working as the last resort.

   Each of these writes an audit event with actor, subject and the number of sessions, never
   an identifier.
10. **Restore.** A restored database may bring back sessions that were ended after the backup.
    Every restore, manual (runbook) or by the updater's rollback, is followed by
    `sessions end-all --reason restore`; a test with a fake covers the updater. The window is
    at most `admin_session_hours`, so no separate epoch is kept outside the backup.
11. **Clean-up by the worker.** The housekeeping job deletes rows that are ended or past their
    absolute end. History is in `audit_events` (login, logout, ended-by events).
12. **Cookie attributes** (fixed security rules, not settings): name `__Host-op_admin`,
    `Path=/`, no `Domain`, `HttpOnly`, `SameSite=Lax`, and `Secure` when `PUBLIC_BASE_URL`
    starts with `https://`, whatever `ENVIRONMENT` says. Development over `http://localhost`
    keeps `op_admin` without `Secure`, because browsers refuse a prefixed cookie without it.
    `Path=/` means the cookie also travels to `/g/...` and `/b/...`. That is acceptable because
    it holds a reference only, and a test asserts that no route outside `/admin` reads or
    writes `request.session`. This step comes after the register so the wider cookie never
    carries claims.
13. **Offboarding (V7.4.2).** Switching a person off at the provider is followed by the idle
    limit at the latest, and by the administrator's *End all sessions* (point 9b) at once.
    OIDC back-channel logout would end sessions on the provider's signal. It is an optional
    later step that needs support at the provider (external evidence), and has no step in the
    table below.
14. **Fail closed.** When the database cannot be read, there is no session and so no access.
    The portal does not run without the database anyway; this is stated so that no cache is
    ever added that keeps a session valid when the register is unreachable.
15. **Roll-out.** Everyone must log in once more after step 1 (old cookies hold no `sid`). The
    PR and the release note say so.

### ASVS 5.0.0 coverage

| Requirement | Covered by |
|---|---|
| V7.1.1, V7.1.3, V7.6.1 (policy, lifecycle, federation) | This ADR (points 3 to 5, 13), which supersedes ADR 0013 §5; the code in step 1 |
| V7.1.2 (parallel sessions) | Point 8: step 1 (limit rule), step 3 (visible, endable) |
| V7.2.4 (new identifier at login) | Point 6: step 1 |
| V7.3.1 (idle timeout) | Points 3 and 4: step 1 |
| V7.4.1 (logout revokes) | Point 7: step 1 |
| V7.4.2 (offboarding) | Points 9b and 13: step 3, with the idle limit from step 1 |
| V7.4.5 (administrator ends sessions) | Point 9b: step 3 |
| V7.5.2 (person sees and ends own sessions) | Point 9a: step 3 |
| V3.3.1, V3.3.3 (cookie prefix, `Secure`, no `Domain`) | Point 12: step 4 |

Left to external evidence: V7.4.3 and V7.5.1 (the provider's re-authentication and MFA
behaviour).

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `admin_idle_minutes` | C platform | 60 | 15-120 | superuser (`settings.platform.manage`), topic *Beheersessie* next to `admin_session_hours` | Product owner's choice. Lower bound 15 so the portal stays usable; upper bound 120 so idle stays well below the absolute 8 h. |
| `admin_max_sessions_per_person` | C platform | 5 | 1-20 | same | Product owner's choice. At 1 each login replaces the previous session. |

Unchanged: `admin_session_hours` (C, 8, 1-8), `admin_reauth_minutes` (C, 60, 5-60), and
`staff_last_seen_interval_seconds` (reused for the write granularity).

**Fixed on purpose:** the cookie name, path and attributes (security rules), 32 random bytes
and `sha256` (crypto parameters), the `Secure` rule derived from `PUBLIC_BASE_URL`, and the
fail-closed behaviour. A protection is never switched off from the portal: no setting disables
the register, the idle check or the revocation at logout.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #337 | merged |
| 1 | Register, logout revokes, rotation at login, idle and absolute check, settings, data-flow entry (WP3) | #350 | merged |
| 2 | Worker clean-up, `sessions end-all`, restore and rollback call it, runbook (WP4) | | planned |
| 3 | Sessions viewable and endable: own list, administrator action, new permission, matrix, manual (WP5; a decision PR because it changes `authorization.py`) | | planned |
| 4 | Cookie `__Host-op_admin`, `Path=/`, `Secure` on https (WP6) | | planned |

Order of building: 1, then 4, then 2, then 3.

## Consequences

- ADR 0013 §5 is superseded by this ADR; its Status line says so, its text is not rewritten.
- A stolen or copied cookie stops working at logout, at the next login, at the idle limit, and
  when a person or administrator ends the session. Step 1 carries a replay test: copy the
  cookie, log out, open `/admin/rights` with the copy.
- New table and migration (reversible), a lookup per admin request, a periodic write for
  `last_seen_at`. `docs/dataflow.md`, `docs/threat-model.md` (abuse cases: replay after logout,
  ending a session of someone outside one's scope), `docs/runbook.md` (restore, offboarding,
  secret rotation), `docs/settings.md`, `docs/authorization.md` and the manual are updated by
  the steps that build them.
- A form posted after an idle time-out is lost, since there is no warning without JavaScript.
  The 60 minute default limits this.
- Everyone logs in once more after step 1, and once more after step 4 (new cookie name).
