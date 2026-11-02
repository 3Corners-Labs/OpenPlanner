# 0037. Closure page: prepared scenarios, alternative contact options and an image

Status: accepted (2026-10-27), decided by the product owner. Extends ADR 0024 (emergency
closure); everything there that this record does not change still holds.
Uploads amended by ADR 0092.

## Context

ADR 0024 gives an emergency closure one thing to say to citizens: a plain-text notice in Dutch
and English. In a real emergency that is not enough, and it is written at the worst moment:

- Citizens who cannot book need to know **how else** to reach the organisation: a phone number
  with its hours, a web form, a chat, a desk that is open.
- The same few situations come back (outage, strike, building closed, whole team absent). Writing
  the text, translating it and checking the phone number under pressure produces mistakes.
- Communication teams want a recognisable image on the page (a pictogram for "closed" or
  "outage"), as on the rest of the organisation's website.
- An organisation wants one default, and a group sometimes needs its own (another desk, another
  number).

The product owner asked that a closure "starts another flow": a page per group or organisation
with its own text, alternative contact options and an image, configured by the organisation
itself.

Constraints that stay: admins never edit HTML or templates (ADR 0009); no personal data outside
`Booking` (CLAUDE.md); every citizen page meets WCAG 2.2 AA in nl and en.

## Decision

1. **Contact options are structured, not free text.** A `ContactOption` is one of a fixed set of
   kinds, each with its own validated fields and an optional label per locale:

   | Kind | Fields | Rendered as |
   |---|---|---|
   | `phone` | number (digits, spaces, `+`, `-`), hours text | `tel:` link plus hours |
   | `email` | functional address | `mailto:` link |
   | `website` | `https://` URL | link |
   | `chat` | `https://` URL | link |
   | `address` | up to four lines, hours text | address block |

   At most six options per set, kept in the order the admin gives. URLs must be `https`;
   anything else (`javascript:`, `data:`, `http:`) is refused. The admin page says that addresses and
   numbers must be functional ones of the organisation, never a person's; the form hint repeats
   that they are shown publicly.

2. **One contact block, used in three places.** A `ContactBlock` (heading per locale, contact
   options) is the shared component of this decision and of maintenance mode (ADR 0038):
   - **Organisation default:** "Contactalternatieven" on the organisation page, edited with
     `organisation.closure` (organisation admin and superuser).
   - **Group override:** on the group's closure page, edited with `closure.manage`. When empty,
     the organisation default applies.
   - **Closure:** each closure holds its own copy (see 4).

3. **Image with a required text alternative.** A closure or scenario may carry one image: PNG,
   JPEG or WebP, recognised by its magic bytes (as the logo, ADR 0020), at most 500 kB, served
   from our own origin with `X-Content-Type-Options: nosniff`, `default-src 'none'` and the type
   from the magic bytes; an upload above the limit is refused unread (413), as for the logo.
   Metadata (EXIF, XMP, PNG text chunks) is stripped before storing, because a photo can carry
   a location or a photographer's name. Only the image of a visible closure is public; the
   images of scenarios are shown to admins only, so a prepared "strike" scenario does not leak
   before it is used.
   The admin gives an alt text in nl and en, or ticks "decorative" (then `alt=""`). No external
   image URLs, so the CSP keeps `img-src 'self'` and no third party sees who opens the page.

4. **Prepared scenarios.** A `ClosureScenario` is a named, reusable closure content: notice and
   optional heading per locale, contact block, image.
   - Scenarios belong to an organisation (managed with `organisation.closure`) or to one group
     (managed with `closure.manage`). A group admin sees and can use the organisation's
     scenarios and the group's own; they cannot change the organisation's.
   - Starting a closure means picking a scenario (or "empty") and adjusting it before
     confirming. The content is **copied** into the closure at that moment: editing a scenario
     later never changes a running closure, and what citizens were mailed stays what the
     closure shows.
   - Closing a whole organisation uses one organisation scenario for every group; each group
     still gets its own closure (ADR 0024 §2) and can be adjusted afterwards.
   - New installations get no prefilled scenarios; the manual gives examples to copy.

5. **What citizens see.**
   - **Active closure:** the group page shows the closure page instead of the calendar: the
     heading, the notice, the image and the contact options, in that order. One heading
     hierarchy, contact links at least 24×24 px, no information in the image only.
   - **Upcoming closure:** the existing banner, with a link to the full closure page; the
     calendar stays available before the start.
   - **Management page of a suspended booking:** the same block above the "choose a new time"
     and "cancel" actions.
   - **Emails** (suspension, update): the notice and the contact options as a fixed text block in
     the HTML and plain-text parts (ADR 0009). The image is not mailed: remote images are often
     blocked and attachments make the mail heavy; the mail links to the closure page instead.

6. **Validation, audit and privacy.**
   - Every text has the limits of ADR 0024 (notice 1000 characters, heading 120, label 80,
     hours text 200, no control characters); the domain validates, the routes only map form
     fields.
   - Changes to scenarios, contact blocks and images are audited with keys and counts
     (`closure.scenario.update,contacts:3`, `closure.start,scenario:4,bookings:5`), never texts
     or numbers.
   - Contact options and images are organisation data, not personal data; they have no purge
     job. The retention rules of ADR 0024 §5 are unchanged.

## Consequences

- An emergency closure becomes a choice of a prepared scenario plus a check, instead of
  writing under pressure. Organisations must prepare their scenarios beforehand; the manual and
  the organisation page prompt for at least a default contact block.
- `ContactBlock` is the second use of "how else to reach us" (closure, maintenance), so it gets
  one home in the domain and one template partial; the direct-call "queue full" message
  (ADR 0026) may use it later.
- New tables: closure scenarios, contact blocks, closure images; the closure row gains a heading,
  a contact block and an image reference. Migrations are reversible; downgrading keeps only the
  notices, as in ADR 0024.
- The closure page and the suspended management page are added to `e2e/pages.json`, with and
  without an image, in nl and en.
- The threat model gets abuse cases for the new inputs: a `javascript:` or `http:` link in a
  contact option, an image that is really HTML or SVG, and a group admin changing an
  organisation scenario. Each gets a test.
