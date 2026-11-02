# 0094. A cryptographic inventory, and key rotation without loss

Status: accepted (2026-11-01), requested by the product owner after the ASVS 5.0.0 L2 audit of
1 November 2026 (V11.1.1, V11.1.2, V11.2.2, V11.4.1, V11.4.3). Amends ADR 0008 (integration
secrets) and ADR 0011 (management tokens). Partly built; see *Build status*. §7 defers the
`SESSION_SECRET` rotation to ADR 0098 (proposed), which does not decide it yet.

## Context

The audit found the cryptography itself sound but undocumented as a whole. It asks for an
inventory of keys, algorithms and certificates, with owner, purpose, storage, rotation and
destruction (V11.1.x). It also found that a key cannot be replaced without loss (V11.2.2).
Reading the code for this decision showed that both rotations are worse than the runbook
says:

- **`SETTINGS_ENCRYPTION_KEY`.** It is stretched with HKDF-SHA256 into one AES-256-GCM key
  (`app/adapters/crypto.py`). Sealed records carry no key id. A new key makes every stored
  integration secret and Webex token unreadable; the runbook's answer is to re-enter them all.
- **`MANAGEMENT_TOKEN_KEY`.** The runbook says rotating it "invalidates every link in mails
  already sent". In fact the lookup goes by the stored `sha256(token)`, so old links keep
  working. What breaks is every message sent after the rotation: confirmation, reminder and
  change mails derive the token again under the new key (`app/jobs/messages.py`), and that
  token matches no stored hash. A participant gets a reminder with a dead link. The two keys
  derived from it with HKDF (client pseudonyms of ADR 0053, agent pseudonyms of ADR 0043)
  change too, so an agent no longer sees "claimed by me" on earlier claims.

The configuration package (ADR 0089) already opens every sealed secret and seals it again
under another key. That is the second use of the same operation.

## Decision

1. **`docs/cryptography.md` is the inventory.** One row per key or algorithm: name, purpose,
   algorithm and parameters, where it lives, who can read it, owner, rotation, destruction,
   and what may never be protected with it. At the time of this decision it lists:
   - `SETTINGS_ENCRYPTION_KEY`: HKDF-SHA256 → AES-256-GCM, 96-bit random nonce, record name
     as associated data;
   - `MANAGEMENT_TOKEN_KEY`: HMAC-SHA256 tokens, plus HKDF labels for the client and agent
     pseudonym keys;
   - `SESSION_SECRET`: the `itsdangerous` signature of the admin cookie;
   - the OIDC client secrets;
   - API client secrets and access tokens: SHA-256 of high-entropy random values;
   - webhook secrets: Webex HMAC-SHA1, WxCC HMAC-SHA256;
   - the configuration package: scrypt → AES-256-GCM;
   - PKCE S256, and ID-token signatures with asymmetric algorithms only;
   - the TLS keys (ACME or own) and image signing (cosign, keyless).

   A PR that adds a key, an algorithm or a parameter updates the inventory; `docs/threat-model.md`
   links to it.
2. **Sealed records carry a key id.** Each sealed value stores the id of the key that sealed
   it: the first 8 bytes of SHA-256 over the derived AES key. Records written before this
   change count as the current key's. Opening picks the key by id from the current key and
   `SETTINGS_ENCRYPTION_KEY_PREVIOUS`.
3. **`python -m app.cli rekey` seals everything again under the current key.**
   - It runs on the host, is idempotent, and writes one system audit event with counts only.
   - It uses the same re-sealing as the configuration package; that code moves to one place.
   - System status shows how many records are still under the previous key, so the
     administrator knows when `SETTINGS_ENCRYPTION_KEY_PREVIOUS` may be removed.
4. **A booking keeps one link for its whole life.** When a message builds the link, it
   derives the token under the current key and, if that does not match the stored hash,
   under `MANAGEMENT_TOKEN_KEY_PREVIOUS`. New bookings use the current key. Agent pseudonyms
   are compared under both keys while the previous one is set. Client pseudonyms (security
   events) simply start anew. System status shows the number of open bookings still on the
   previous key; at zero the previous key may go.
5. **Webex's HMAC-SHA1 is a recorded provider exception.** Webex signs webhooks with
   HMAC-SHA1 and offers nothing else. HMAC-SHA1 is not broken as a MAC, but ASVS lists SHA-1
   as not approved. The webhook secret is random, the comparison is constant-time, replays are
   caught by `event_key`, and the inventory names the exception. When Webex offers SHA-256,
   the adapter switches.
6. **Agility is by label and id.** Every derivation has a versioned label (`…/v1`). A new
   algorithm or parameter gets a new label and a new key id; old data stays readable until
   `rekey` has run. That is how a later move to post-quantum primitives would go.
7. **`SESSION_SECRET` is out of scope here.** Its rotation still logs everyone out. The admin
   session redesign that the ASVS session work proposes decides its algorithm and rotation;
   the inventory records the current `itsdangerous` HMAC-SHA1 until then.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `SETTINGS_ENCRYPTION_KEY_PREVIOUS` | A bootstrap | empty | at least 32 bytes | — | a bootstrap secret: needed before the database can be read; env or `X_FILE` only |
| `MANAGEMENT_TOKEN_KEY_PREVIOUS` | A bootstrap | empty | at least 32 bytes | — | as above; set only during a rotation |

**Fixed on purpose:** algorithms, key lengths, nonce size, HKDF labels and the key-id length
are crypto parameters; they change only through this inventory and a new label.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted | #309 | merged |
| 1 | `docs/cryptography.md`; runbook *Rotating secrets* written for the new procedure (the wrong statement about `MANAGEMENT_TOKEN_KEY` is corrected in the PR of this ADR) | | planned |
| 2 | Management links under the previous key (point 4), with a test that a reminder after rotation carries a working link | key rotation | planned |
| 3 | Key id on sealed records, `SETTINGS_ENCRYPTION_KEY_PREVIOUS`, `rekey`, shared re-sealing with the configuration package. Built without a migration: the key id is appended to the stored nonce (12 bytes without an id, 20 with), so no column is added and old values stay readable. The System status count is not built yet | key rotation | planned |

## Consequences

- Rotating either key becomes a routine step: set the previous key, set the new key, restart,
  run `rekey` (settings key) or wait for open bookings to finish (token key), then remove the
  previous key.
- Step 2 fixes a defect that exists today: until it is built, the runbook must say not to
  rotate `MANAGEMENT_TOKEN_KEY` while bookings are open, except after an incident.
- Step 3 adds a migration (a key-id column next to every sealed value); it is reversible.
- The inventory is a document to keep current; the PR template's review question on
  dependencies covers new cryptographic libraries.
