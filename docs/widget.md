# Embedding and styling

Status: the standalone pages (`/g/{slug}`) use the tokens below since WP3b (ADR 0020). The
embeddable Web Component `<gov-booking>` (WP3c, ADR 0035) uses the same tokens; how a host
website embeds it, its `::part()` names and the CSP a host needs are in
[Embedding the widget](#embedding-the-widget-on-your-website-adr-0035) below.

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
| `--booking-font-family` | system UI stack | Text (the widget uses `inherit`: the host page's font) |
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

## Embedding the widget on your website (ADR 0035)

The standalone page `/g/{slug}` is the default: link to it from your website and you are
done. If you would rather show the calendar inside a page of your own website, use the Web
Component `<gov-booking>`. It runs in your page, inherits your font and takes your colours
through the tokens below, and talks directly to OpenPlanner: what citizens type never passes
your servers. There is no iframe variant; a site that cannot add a script uses the link.

### 1. Allow your website and switch the group on

Two things happen in the OpenPlanner admin portal before the widget shows anything:

1. An organisation admin adds your website under **Insluiten** (`embedding.manage`): the
   origin exactly as the browser sends it, `https://www.gemeente.nl`. Scheme, host and port
   must match; there are no wildcards, and `https://werk.gemeente.nl` is a website of its own.
   Only `https`; `http://localhost` is accepted on development and test installations only.
2. The group switches **Insluiten op websites** on in its settings (`group.edit`).

On any other website, or for a group that is not switched on, the widget says that the
calendar is not available here and offers no booking. A copied tag on another site does not
work.

### 2. Add the code

The **Insluiten** page shows the code for your organisation, with the current hash:

```html
<script type="module" src="https://afspraak.example.nl/widget/v1/gov-booking.js"
        integrity="sha384-…" crossorigin="anonymous"></script>

<gov-booking organisation="gemeente-voorbeeld" group="burgerzaken"></gov-booking>
```

Load the script once per page; place one element per group where its calendar belongs.

| Attribute | Required | Meaning |
|---|---|---|
| `organisation` | yes | Your organisation's slug, as shown in the code on the Insluiten page |
| `group` | yes | The group's slug (the last part of `/g/{slug}`) |
| `lang` | no | `nl` or `en`. Without it: the nearest `lang` attribute of an ancestor, then `<html lang>`, then `nl` |
| `heading-level` | no | Level of the widget's headings, 2–6 (default 2), so they fit your page outline; sub-headings are one level lower |

Changing `organisation`, `group` or `lang` later restarts the widget.

**Versions and integrity.** The path carries the major version (`/v1/`); attributes and
parts stay compatible within it. The file itself changes with every OpenPlanner update, and
so does its hash. With `integrity`, the browser refuses a script whose hash differs: take
over the new code from the Insluiten page after each update, or leave `integrity` out and
rely on HTTPS. `crossorigin="anonymous"` is required in both cases (the script is a module).

### 3. Your Content Security Policy

The widget needs exactly two additions to your CSP, for the OpenPlanner origin:

```text
script-src  'self' https://afspraak.example.nl;
connect-src 'self' https://afspraak.example.nl;
```

No `'unsafe-inline'`, no `'unsafe-eval'`, no `style-src` change: the widget's styles are a
constructable stylesheet inside its shadow root, which CSP does not govern, and it never sets
inline styles. The test host page in `e2e/host/` runs with exactly this policy. The script is
served with `Cross-Origin-Resource-Policy: cross-origin`, so a page with
`Cross-Origin-Embedder-Policy: require-corp` can load it too. Framing stays forbidden on every
OpenPlanner page (`frame-ancestors 'none'`).

### 4. Styling

Everything visual reads the `--booking-*` tokens of the table at the top of this document,
with the same defaults as the standalone pages. Custom properties inherit into the shadow
root, so set them on the element or any ancestor:

```css
gov-booking {
  --booking-color-accent: #8a1538;
  --booking-radius: 2px;
}
```

The widget uses `font-family: inherit`: it takes your page's font, unless you set
`--booking-font-family-heading` for its headings. `--booking-target` (44 px) and the focus
colours keep their defaults unless you set them; keep the focus colours at 3:1 contrast and
targets at least 24 × 24 px (WCAG 2.2, 2.4.11 and 2.5.8).

For anything the tokens do not cover, these parts can be styled with `::part()`:

| Part | Element |
|---|---|
| `root` | The widget's region |
| `heading` | Every heading (step titles, month, times, form summary) |
| `notice` | Closure notice, "not available here", and the confirmation |
| `quick-pick` | "First possible" with its button |
| `button` | Buttons (first possible, month navigation, submit, retry) |
| `calendar` | The month grid |
| `day` | Every day in the grid; also `day-free` for days with free times and `day-selected` for the chosen day |
| `time` | A time button |
| `form` | The contact form |
| `label`, `input` | Labels and inputs of the form |
| `error`, `error-summary` | A field's message and the summary above the form |
| `submit` | The submit button (also `button`) |
| `link` | Links and link-styled buttons ("choose another time", the standalone page) |

Do not use `::part()` to hide information or reduce contrast; the widget is tested for WCAG
2.2 AA with its defaults and with a host house style (`e2e/host/host.css`).

### 5. What the widget does

Step 1, a time: the group's name and introduction, a closure notice if the group is closed
or will close (no booking while closed), "first possible", the month grid and the free
times of the chosen day. Step 2, details: first and last name, email, per group phone, and the
group's own questions (text, choice list, checkbox, date …; ADR 0055), with the same
validation as the standalone page. Every input, select and textarea is exposed as
`part="input"`, so your `::part(input)` rule styles them all; a checkbox sits before its label. Step 3, done: the appointment and "you will receive a
confirmation by email". The management link (view, move, cancel) arrives by email only; it is
never shown to or readable by your page.

Not in the widget: starting a call right away (O9) and the time filters. The widget links to
the group's standalone page for those.

Accessibility inside the shadow root: every label, role and live region is inside the
component; the grid is one tab stop (arrow keys, Home/End, Page Up/Down, Enter or Space);
focus moves to the heading of each next step; an `aria-live` region announces the free times
of a chosen day, errors and the result; no time limits; `prefers-reduced-motion` and
`prefers-contrast` are honoured.

### 6. Privacy

Your website learns that a visitor opened a page with the widget and which group the tag
names, as for any element on your page. The widget sends no cookies, and the citizen's name,
email, phone and answers go from their browser to OpenPlanner directly (`docs/dataflow.md`). Answers
to your page hold no personal data and no management token.

For a client of your own (not the Web Component): `POST …/bookings` takes `first_name`,
`last_name`, `email`, an optional `phone` and `answers`, an object of the group's field keys
(the `name` in the group answer without `q_`) to strings; a ticked checkbox is `"on"`. The
older `name` is still accepted and split at the first space. A refused answer is
`{"error": "invalid_answer", "field": "q_<key>"}`.

### 7. When it says "not available here"

- The page's origin is not on the organisation's list (check scheme, `www.`, port).
- The group is not switched on for embedding, or the slugs in the tag are wrong.
- The browser blocked the script or the API: check the console for CSP messages and that
  `crossorigin="anonymous"` is on the script tag.
- After an update with a pinned `integrity`: copy the new code from the Insluiten page.

During maintenance of OpenPlanner (ADR 0038) the widget says instead that booking is briefly
unavailable because of maintenance, with a retry button; the standalone page shows the same.

The API behind the widget is described in `docs/api.md` (`/api/v1/widget`).
