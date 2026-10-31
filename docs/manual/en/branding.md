# Branding

On this screen you set how an organisation's booking pages look: name and logo in the header,
colours, fonts and the shape of cards and buttons. Open it via **Organisations → organisation → Branding**.
Only a superuser can change branding; the portal may ask you to sign in again before saving.
The admin portal itself keeps one fixed style.

At the top you see a preview of the branding as it is saved now. A change applies at once to
every group of the organisation.

Uploading free CSS is not possible. Free CSS can hide labels, remove the focus outline or leak
data to another website. The pages must stay accessible (WCAG 2.2 AA), safe and
privacy-friendly whatever the branding. So you choose from a fixed list of settings, which the
portal checks.

## Name, header and colours

<!-- field: display_name -->
### Name in the header

The name participants see at the top of every page. Leave it empty to show the organisation's
name.

<!-- field: header_style -->
### Header

- **Logo and name, with a line in the accent colour**: the default.
- **Logo and name on a bar in the accent colour**: the whole header gets the accent colour,
  the text the colour set under "Text on the accent colour".
- **Logo only**: for a logo that already contains the name. The name stays available to
  screen readers. Without a logo the page shows logo and name as usual.

<!-- field: accent_color -->
### Accent colour

For buttons, links and the chosen day. The colour needs a contrast of at least 4.5:1 with the
text on it and with the backgrounds it is used on as a link colour.

<!-- field: color_text -->
### Colours

Pick colours with the colour picker. When saving, the portal checks every pair that appears
together on the page: text on the page, on cards and in inputs at least 4.5:1, input borders
and the focus outline at least 3:1. If a pair fails, the portal names the colour you changed.
A dark theme is not possible: the yellow focus highlight needs dark text.

People who ask their operating system for more contrast get the default colours with extra
contrast. Only the accent colour stays.

## Shape and text

<!-- field: font_size -->
### Text size

In pixels, from 16 to 20. Smaller than the browser default is not possible; participants who make
their browser text larger still get larger text.

<!-- field: radius -->
### Corner radius

How round the corners of cards (0 to 24 pixels) and of buttons and inputs (0 to 16 pixels)
are. 0 gives square corners.

Line height (1.4 to 2), heading weight, maximum width (640 to 1440 pixels) and the shadow
under cards are set here too. Spacing between parts and the size of buttons are fixed: they
keep the page easy to use with a keyboard and a finger.

## Logo

PNG, JPEG or WebP of at most 200 kB. SVG is not possible, because an SVG file can contain
scripts. A landscape logo of about 80 pixels high works best.

## Fonts

You can upload three fonts: for regular text, for bold text and for headings. WOFF2 only, at
most 250 kB per file. Without a bold font the browser makes the regular font bolder itself;
without a heading font, headings use the text font.

Fonts are loaded from this service, never from another website such as Google Fonts, so a
participant's computer does not contact other parties. Make sure the font's licence allows using
it on your own server.

## Theme file

For those who prefer to keep the branding in a file, for example for several environments:

1. Click **Download current theme**. You get a CSS file with every setting; settings at their
   default are in it as comments.
2. Change the values. Remove the comment markers around a line you want to change.
3. Upload the file under **Apply theme file**.

<!-- field: theme_file -->
### What the file may contain

Only lines such as `--booking-radius: 4px;`, with a name from the downloaded file and a valid
value, optionally inside one `:root { ... }`. Comments are fine. Selectors, `@import`, `url()`
and `!important` are not. On an error the portal names the line. The file replaces the whole
theme: what it leaves out returns to its default, the accent colour included. Logo and fonts
stay. Font families (`--booking-font-family`) can only be set through the file.
