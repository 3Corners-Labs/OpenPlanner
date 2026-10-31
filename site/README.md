# openplanner.nl

The public website (ADR 0087): one page per locale, static, built from this directory by
`scripts/build_site.py` and published by the `site` job of `.github/workflows/main.yml`.

- `content/nl.json`, `content/en.json`: every visible text and list; both files have the same
  keys (a test checks it).
- `templates/index.html`: the page, Jinja2.
- `static/site.css`, `static/site.js`: the only stylesheet and the only script (progressive
  enhancement; the page is complete without it).
- `static/og-nl.png`, `static/og-en.png`: Open Graph images, rasterised from `og/*.svg` with
  `rsvg-convert -w 1200 -h 630 og/og-nl.svg -o static/og-nl.png`.
- `static/media/`: the recorded walkthrough of the real interface (ADR 0087 step 3), not yet.

Build locally with `make site` and open `site/dist/index.html` through any static server, for
example `python3 -m http.server -d site/dist 8080`.

Rules: no cookies, no storage, no request to another origin, CSP `default-src 'self'`,
WCAG 2.2 AA. The brand mark is copied from `app/web/static/brand/` at build time.
