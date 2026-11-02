# Assurance package for external parties

Some requirements of the ASVS 5.0.0 level 2 audit of 1 November 2026 cannot be proven from this
repository: they depend on the client's identity provider, the host, Cisco, or the data
controller. This folder turns each of them into a checklist that its owner can fill in. Every
such requirement is in exactly one checklist, and a test keeps it that way
(`tests/test_assurance_docs.py`).

## The files

| File | For | Contents |
|---|---|---|
| `idp.md` | The client's IdP administrator and CISO | Password, second factor, OAuth and OpenID provider requirements, with the Keycloak reference of the bundled realm |
| `hosting.md` | The hosting party and security operations | TLS at the edge, backend traffic, components, time, logs, shared trust boundaries, deletion from files and snapshots |
| `cisco.md` | The client's integration administrator, who asks Cisco | Media and signalling requirements, what Cisco receives, settings to confirm in Control Hub |
| `dpia-input.md` | The controller and its data protection officer | Pointer to the retention matrix, decisions on retention and installations, the processor template per recipient |
| `pentest-scope.md` | The penetration tester | What to test; not a checklist |

## How to use a checklist

1. The client names an owner for each file before go-live (IdP administrator, hosting party,
   integration administrator, data protection officer). Without a named owner a checklist has
   no use; put it in the go-live checklist and in the tender.
2. Each row has an ID, what is asked in short (our own words; the full text is in the OWASP ASVS
   5.0.0, licensed CC BY-SA 4.0), how to show it, the owner, and a reference. The reference
   points to the OpenPlanner document or change that is the source, or to the Keycloak setting
   of the bundled realm (ADR 0090) to compare with.
3. The owner runs the command or takes the export, stores it with the date and the version of
   OpenPlanner it was taken against (System status), and writes the location in the sign-off
   table at the end of the file. Do not paste secrets, tokens or personal data into the
   evidence; a screenshot of a setting is enough.
4. A row is closed only by evidence. "Not applicable" needs a reason, written in the evidence
   reference and signed by the owner.
5. Repeat the checklists at each major release, after a change of identity provider or host, and
   yearly.

## What the application already proves

The rows say where OpenPlanner gives a command or a document. In short: `oidc-check` (login
configuration and the pinned issuer, ADR 0097), the second-factor check (ADR 0100), server-side
sessions (ADR 0098), `address-check` and the *Address and proxy* block (ADR 0086),
`docs/cryptography.md` (ADR 0094), the outbound table in `docs/dataflow.md` (ADR 0091), the
retention matrix and the notes on deletion from SQLite (`secure_delete`, ADR 0102), and the
back-up and restore procedure in `docs/runbook.md`. Anything not built yet is said so in the
file it concerns; nothing is claimed without a source.

## Not covered here

The requirements the application can prove itself are in the audit evidence register and the
tests, not in this folder. The one-command evidence report of the application (`evidence`,
`oidc-check --evidence`) and the browser test that public pages call no third party are separate
work packages; when they exist, the rows that cite them are updated in the same pull request.
