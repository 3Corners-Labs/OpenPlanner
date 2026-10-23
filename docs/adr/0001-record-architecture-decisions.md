# 0001. Record architecture decisions

Status: accepted (2026-10-23)

## Context

Decisions B1–B16 in `docs/HANDOVER.md` were made before the repository existed. Later decisions
need a place where their reasoning survives the conversation that produced them.

## Decision

Every architecturally significant decision gets one file `docs/adr/NNNN-title.md` with status,
context, decision and consequences. ADRs are numbered sequentially and never rewritten; a
changed decision gets a new ADR and the old one is marked `superseded by NNNN`.
The decisions in HANDOVER §2 stand as the baseline and are not repeated here.

## Consequences

Changing the stack, the public API (`/api/v1`) or a license exception requires an ADR in the
same PR.
