# 0011. Management tokens derived from a per-booking salt and a server key

Status: accepted (2026-10-23). Refines the management-token rule in CLAUDE.md.

## Context

The citizen's change/cancel link `/b/{token}` must appear in every message: confirmation,
change, reminder. Messages are sent by background jobs, possibly days after booking. CLAUDE.md
required tokens to be 32 random bytes with only their SHA-256 stored, and forbids secrets in
job payloads. With a purely random token, nothing can put the link in a later message.

## Decision

1. Per booking a random salt of 32 bytes (URL-safe) is stored in
   `bookings.management_token_salt`.
2. The token is `base64url(HMAC-SHA256(key, "manage:" + salt))`, where `key` is the bootstrap
   secret `MANAGEMENT_TOKEN_KEY` (at least 32 bytes; env only, ADR 0008).
3. The database also stores `sha256(token)` for lookups; comparison uses
   `hmac.compare_digest`. The token itself is never stored or put in a job.
4. Without `MANAGEMENT_TOKEN_KEY` in development, web and worker share a random key generated
   once in the data volume (`data/dev-token.key`). In production the key is required; startup
   fails without it.

## Consequences

- A copy of the database alone cannot produce working links; the key is needed as well.
- Rotating `MANAGEMENT_TOKEN_KEY` invalidates all outstanding links. The runbook describes this;
  bookings remain intact.
- A reschedule creates a new booking with a new salt, so the old link points to the cancelled
  booking and shows only its status.
