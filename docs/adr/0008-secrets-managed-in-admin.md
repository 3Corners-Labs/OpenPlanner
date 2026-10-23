# 0008. Integration secrets managed in the admin UI, encrypted at rest

Status: accepted (2026-10-23). Refines the "secrets via env only" rule in CLAUDE.md.

## Context

Superusers must configure SMTP, the WxCC API and messaging channels per organisation without a
redeploy (HANDOVER §6). Some of those settings are secrets (SMTP password, OAuth client
secrets, API keys). CLAUDE.md so far allowed secrets only in the environment or a secret store.

## Decision

1. **Bootstrap secrets stay in the environment:** the settings encryption key, the OIDC client
   secret of the admin portal and the session secret. They are never editable in the UI.
2. **Integration settings live in the database.** Non-secret fields (host, port, sender, queue
   IDs) are stored as plain columns. Secret fields are encrypted with AES-256-GCM (`cryptography`,
   Apache-2.0/BSD). The key comes from `SETTINGS_ENCRYPTION_KEY`. Every value stores a key ID,
   so keys can be rotated by re-encrypting.
3. **Write-only in the UI.** A secret is entered or replaced, never shown again; the UI shows
   only "set" or "not set" and when it was last changed.
4. **Audit and logging.** Every change writes an audit event (actor, organisation, setting
   name, never the value). Secret values never reach logs, error messages or job payloads.
5. **Scope.** Only the superuser role may change integration settings (HANDOVER §6).

## Consequences

- Implemented in work package 7, together with the admin portal.
- `docs/runbook.md` gets a key-rotation procedure; losing the key means re-entering all
  integration secrets.
- CLAUDE.md "Secrets via env/secret store only" now reads "bootstrap secrets via env; integration
  secrets encrypted in the database per ADR 0008".
