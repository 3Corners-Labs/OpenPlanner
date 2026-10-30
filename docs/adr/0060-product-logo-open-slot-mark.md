# 0060. Product logo: the open-slot mark

Status: proposed (2026-10-30), requested by the product owner. Closes the open point *Logo* of ADR 0047.

## Context

ADR 0047 left the portal on a neutral "OP" text mark until the product owner chose a logo. The portal, the citizen pages ("Appointments via OpenPlanner"), emails and the favicon need one mark that works at 16 px, in one colour, and next to a municipality's own logo without competing with it. The design project explored four directions; the product owner chose direction A.

## Decision

1. **The mark** is a 3 × 3 grid of rounded tiles on a 24-unit grid, with one tile left open at the middle right. The open tile is drawn slightly larger, with three rounded corners and one square corner at the bottom right, in the 3corners orange. It reads as "a free slot in the schedule".
2. **The wordmark** is "Open**Planner**" in the system font of the design system (regular + bold). No custom typeface.
3. **Colours** come from tokens: tiles `--adm-accent` (`#01689b`; `#6cb4ee` in the dark theme), open tile a new token `--op-brand-slot` (`#ee7f01`). A one-colour version (all tiles `currentColor`, open tile as outline) exists for print and monochrome use.
4. **Favicon and app icon**: the mark in white on an accent square with rounded corners. At 16 px the open tile is left out, because it would blur. From 24 px it is drawn as an outline.
5. **Files** live in `app/web/static/brand/`: `mark.svg`, `mark-mono.svg`, `favicon.svg`, `favicon-32.png`, `apple-touch-icon.png`. SVGs are hand-optimised, have no scripts or external references, and carry `role="img"` with a text alternative where they are not decorative.
6. **Where the mark appears**: the admin top bar (replacing "OP"), the admin favicon, the citizen footer "Appointments via OpenPlanner" (when the organisation keeps it on, ADR 0041), and the plain-text-safe email footer. On citizen pages the organisation's logo always comes first; our mark is secondary.
7. **Motion** (optional, ADR 0061 §6): the open tile may move across the grid and settle (sign-in, booking confirmed) or loop (loading). It never plays when `prefers-reduced-motion: reduce` is set, never blinks more than three times a second (WCAG 2.3.1), and the static mark is always the fallback.

## Settings

None. The mark is product identity, not tunable per installation.

**Fixed on purpose:** the mark geometry, the two brand colours and the 16 px rule (§4). Organisations brand citizen pages through ADR 0041, not by replacing this mark.

## Consequences

- `_shell.html` and `base.html` link the favicon; `layout.html` replaces the "OP" span with `mark.svg` and a visually hidden product name.
- `admin.css` gets `--op-brand-slot` (light and dark value) and the `.admin-brand__mark` image size.
- `REUSE.toml` annotates the brand files (EUPL-1.2; the 3corners corner shape is used with the owner's permission).
- Manual (ADR 0033): no new topic.
- No migration, no dependency.
