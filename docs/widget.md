# Embedding and styling

Status: the standalone pages (`/g/{slug}`) use the tokens below since WP3b (ADR 0020). The
embeddable Web Component `<gov-booking group="...">` follows in WP3c with the same tokens,
plus `::part()` names and the CSP and CORS settings a host site needs.

## Design tokens (`--booking-*`)

All colours, spacing, radius and font of the citizen pages come from these CSS custom
properties (`app/web/static/site.css`). Defaults follow NL Design System conventions.

| Token | Default | Used for |
|---|---|---|
| `--booking-color-text` | `#1a1a1a` | Body text |
| `--booking-color-text-muted` | `#545454` | Hints, secondary text (7:1 on white) |
| `--booking-color-background` | `#ffffff` | Cards, inputs, header |
| `--booking-color-page` | `#f5f6f7` | Page background |
| `--booking-color-surface` | `#ffffff` | Cards |
| `--booking-color-border` | `#d0d4d9` | Decorative borders |
| `--booking-color-border-strong` | `#6b7178` | Input borders (3:1, WCAG 1.4.11) |
| `--booking-color-accent` | `#01689b` | Buttons, links, free and chosen days (organisation theme) |
| `--booking-color-on-accent` | `#ffffff` | Text on the accent |
| `--booking-color-accent-hover` | accent mixed with 18% black | Hover |
| `--booking-color-accent-subtle` | accent at 8% on white | Tints ("first available", hover) |
| `--booking-color-focus` / `-focus-background` | `#1a1a1a` / `#ffbf47` | Focus ring and halo |
| `--booking-color-error`, `-success` (+ `-subtle`) | `#c22a1e`, `#2f7210` | Messages |
| `--booking-color-unavailable` | `#6b7178` | Days without free times |
| `--booking-font-family` | system UI stack | Text (the widget will use `inherit`) |
| `--booking-font-size`, `--booking-line-height` | `1.0625rem`, `1.6` | Text |
| `--booking-space-xs` … `--booking-space-xl` | `0.25rem` … `2.5rem` | Spacing |
| `--booking-radius`, `--booking-radius-small` | `10px`, `6px` | Corners |
| `--booking-target` | `44px` | Minimum size of interactive targets |

`prefers-contrast: more` switches to black text and borders; `prefers-reduced-motion` turns off
transitions.

## Organisation branding

The superuser sets a display name, an accent colour and a logo per organisation (Admin →
Branding). Pages load `/o/{slug}/theme.css`, which sets `--booking-color-accent` only; the
domain refuses colours below 4.5:1 against white. Logos are PNG, JPEG or WebP up to 200 kB and
are served from `/o/{slug}/logo`. Both URLs carry a version, so browsers may cache them.

## Calendar behaviour

See ADR 0020: without JavaScript a table of links; with `static/calendar.js` an ARIA grid
(arrow keys, Home/End, Page Up/Down, Enter or Space to choose a day).
