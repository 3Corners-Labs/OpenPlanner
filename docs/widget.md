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
| `--booking-color-later`, `-later-background`, `-later-stripe` | `#545454`, `#eceef0`, `#dadde1` | Days beyond the booking horizon ("nog niet te boeken") |
| `--booking-font-family` | system UI stack | Text (the widget will use `inherit`) |
| `--booking-font-family-heading` | `var(--booking-font-family)` | Headings and the name in the header |
| `--booking-font-weight-heading` | `700` | Headings |
| `--booking-font-size`, `--booking-line-height` | `1.0625rem`, `1.6` | Text |
| `--booking-space-xs` … `--booking-space-xl` | `0.25rem` … `2.5rem` | Spacing |
| `--booking-radius`, `--booking-radius-small` | `10px`, `6px` | Corners |
| `--booking-target` | `44px` | Minimum size of interactive targets |

`prefers-contrast: more` switches to black text and borders; `prefers-reduced-motion` turns off
transitions.

## Organisation branding

The superuser sets the branding per organisation (Admin → Huisstijl, ADR 0020 and 0041):

- display name, logo (PNG, JPEG or WebP up to 200 kB) and a header style: `standard` (logo and
  name, accent line), `accent` (header filled with the accent) or `logo` (logo only, the name
  in its `alt`);
- the accent colour and the other editable tokens below, checked for contrast across tokens on
  save (text 4.5:1, borders and focus 3:1);
- up to three WOFF2 fonts (text, text bold, heading; 250 kB each), served from
  `/o/{slug}/font/{role}` and declared as `"OpenPlanner Text"` and `"OpenPlanner Heading"`;
- or all tokens at once as a theme file: only `--booking-*` declarations from the list below,
  no selectors, at-rules, `url()` or `!important`.

Pages load `/o/{slug}/theme.css`, generated from the validated values. Colour tokens other
than the accent sit in `@media not (prefers-contrast: more)`, so the high-contrast defaults
win when a citizen asks for them. Theme, logo and font URLs carry a version, so browsers may
cache them.

| Editable token | Values |
|---|---|
| `--booking-color-accent` | `#RRGGBB` |
| `--booking-color-text`, `-text-muted`, `-page`, `-surface`, `-background`, `-border`, `-border-strong`, `-on-accent` | `#RRGGBB` |
| `--booking-font-family`, `--booking-font-family-heading` | family names and generic keywords |
| `--booking-font-size` | 16–20 px (`px` or `rem`) |
| `--booking-line-height` | 1.4–2.0 |
| `--booking-font-weight-heading` | 400–900, steps of 100 |
| `--booking-radius`, `--booking-radius-small` | 0–24 px, 0–16 px |
| `--booking-max-width` | 640–1440 px |
| `--booking-shadow` | `none` |

Spacing, `--booking-target` and the focus colours are not editable: they carry the WCAG
guarantees for target size and visible focus.

## Calendar behaviour

See ADR 0020: without JavaScript a table of links; with `static/calendar.js` an ARIA grid
(arrow keys, Home/End, Page Up/Down, Enter or Space to choose a day).
