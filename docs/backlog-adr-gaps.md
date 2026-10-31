# Backlog: gaps between accepted ADRs and the code

What the check of every ADR against the code found on 2026-10-30 (`docs/adr-status.md`, second
pass): decisions that are accepted but not (fully) built. Each item is one PR with the usual
gates (CLAUDE.md); "done" always includes tests, nl/en texts where users see something, the
manual topic (ADR 0033) and the docs named in the item. When an item is built, the PR updates
the ADR's *Build status*, its Status line and its row in `docs/adr-status.md`. When an item is
dropped, a new ADR says so and marks the old point superseded; nothing is dropped silently.
Sizes: S ≈ up to 2 days, M ≈ up to a week, L ≈ more than a week.

| ID | ADR | Item | Size | Done when | Decision needed | Status |
|---|---|---|---|---|---|---|
| AG-1 | 0004, 0009, 0022 | Organisation text overrides: an `OverrideSource` adapter over a new table, an editor for an organisation's texts (placed per ADR 0047), wired into the translator; the layer order stays catalogue, organisation override, group text. CLAUDE.md says these are editable in the admin UI | M | Override shows on citizen pages and in messages; editor audited and in the manual; `{placeholder}` allow-list enforced; CLAUDE.md true again | Where the editor lives (ADR 0047 organisation tab) | Built (PR #168; ADR 0074) |
| AG-2 | 0019 point 4 | A citizen can leave or cancel a direct call from the waiting page, which ends the work item (`CallService.end_task`) | S | Waiting page has the control; the task ends in WxCC and on the timeline; test per state (waiting, accepted) | – | Built (PR #164) |
| AG-3 | 0010 | Live meeting status: `VideoProvider.meeting_status` as polling fallback when no webhook arrived, and the agenda refresh every 30 s through the polled-fragment pattern of ADR 0072 (`live-region.js`) | M | Status older than 30 s is refreshed on open; fake-provider test; agenda updates without a full reload; CSP unchanged | – | Built (PR #167) |
| AG-4 | 0023 | Change requests for the machine API: `POST` and `GET /change-requests`, scope `bookings:request_change`, the citizen's confirmation through their own link | L | Abuse cases in the threat model (enumeration by phone number, flooding); contract docs in `docs/agent-api.md` | Whether and when (security-sensitive; needs the client's wish) | Not started |
| AG-5 | 0002 | Per-request log line with the route template (never the path or query), and Referrer-Policy `no-referrer` instead of `same-origin` | S | Test asserts no PII or token in the line; header test | Whether `same-origin` is wanted for the admin portal | Not started |
| AG-6 | 0008 | Key id per encrypted value and re-encryption on rotation of `SETTINGS_ENCRYPTION_KEY`, so rotating no longer makes secrets unreadable | M | Rotation test: old and new key side by side, values re-encrypted; runbook updated | – | Not started |
| AG-7 | 0012 | Check that a merge to `main` records the product owner's instruction (a CI check on the merge commit), and bring the ADR's question count (five) in line with the PR template (seven) in a new ADR | S | CI check fails on a merge commit without the instruction; ADR note | Whether a machine check is wanted | Not started |
