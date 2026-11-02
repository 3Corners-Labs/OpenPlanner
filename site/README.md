# openplanner.nl

The public website (ADR 0087): one page per locale, static, built from this directory by
`scripts/build_site.py` and published by the `site` job of `.github/workflows/main.yml`.

- `content/nl.json`, `content/en.json`: every visible text and list, the hand-written roadmap
  and the footer disclaimer; both files have the same keys (a test checks it). The main page
  lists no ADR numbers; a roadmap item must not be built (a test checks `docs/adr-status.md`).
- `templates/index.html`: the page, Jinja2.
- `static/site.css`, `static/site.js`: the only stylesheet and the only script (progressive
  enhancement; the page is complete without it).
- `static/og-nl.png`, `static/og-en.png`: Open Graph images, rasterised from `og/*.svg` with
  `rsvg-convert -w 1200 -h 630 og/og-nl.svg -o static/og-nl.png`.
- `static/media/`: the recorded walkthrough of the real interface and its stills (ADR 0087 step 3),
  downloaded from the newest release that carries them by the `site` job; not in git.

Build locally with `make site` and open `site/dist/index.html` through any static server, for
example `python3 -m http.server -d site/dist 8080`.

Rules: no cookies, no storage, one request to another origin (the public release list from
`api.github.com`, ADR 0110), CSP `default-src 'self'` plus `connect-src https://api.github.com`,
WCAG 2.2 AA. The brand mark is copied from `app/web/static/brand/` at build time.
