# 0087. The public website: static, built from the repository, shipped with every release

Status: accepted (2026-10-31, `po:approved` on #271 and #285), requested by the product owner. Partly built: steps 1 and 3 are on `main` (#285, #289) and step 1 is live on TransIP (first deploy 2026-11-01 by hand; the CI deploy waits for step 4); the first recording is on `v0.4.0-rc.86`; see *Build status*.

## Context

OpenPlanner has a README, a handover document and eighty-odd ADRs, and no place that tells a
visitor in two minutes what it is, what it does, which Webex products it connects to and how to
get it. The product owner wants a modern public website at `openplanner.nl`: the story, the
functions, the key features, enterprise readiness, the open-source licence and the community,
with dynamic interaction, a moving impression of the user interface, and the current release
on it. It is hosted on a TransIP web hosting package, apart from the application, which deploys
what is pushed to its git repository (`openplannernl@openps.ssh.transip.me:auto.git`). It is driven
from Git: the repository is the source, GitHub Actions builds and publishes it, and a release
updates it.

Constraints that shaped the answer: the site may set **no cookies** and may send nothing about
the visitor to a third party; it must meet the same accessibility target as the product
(WCAG 2.2 AA, keyboard, reduced motion); and it must not grow a second stack next to the one
of CLAUDE.md. The repository already has Jinja2, Python and a release workflow; the product
already has a design language (ADR 0060 mark, ADR 0020 tokens, NL Design System defaults).

## Decision

1. **One source, in this repository.** The website lives in `site/`: `site/content/{nl,en}.json`
   (every visible text and list, Dutch default and English, keyed like the product's
   catalogues), `site/templates/` (Jinja2, one layout), `site/static/` (one stylesheet, one
   script, images). No framework, no bundler, no package manager: HTML5, CSS with custom
   properties and `light-dark()`, and vanilla JavaScript as progressive enhancement. The page
   works without JavaScript; JavaScript adds the interactive booking demo, the animated tour,
   scroll reveals and nothing else.
2. **A tested build script renders it.** `scripts/build_site.py` renders every locale into
   `site/dist/` (`/` is Dutch, `/en/` is English, linked with `hreflang`), copies the static
   files and the brand mark from `app/web/static/brand/` (one source for the logo, ADR 0060),
   and bakes the release data in. `make site` runs it; `tests/scripts/test_build_site.py` builds
   it and checks the rules below. The output is plain files that any web server serves.
3. **The language is the URL, not a cookie.** `/` and `/en/` are two trees; the switch is a
   link. No cookie, no `localStorage`, no analytics, no fonts or scripts from another origin,
   no embedded players. The page carries a `Content-Security-Policy` meta tag of
   `default-src 'self'` with `img-src 'self' data:` and `media-src 'self'`. The test fails on an
   external `src`, on `document.cookie` or on web storage in the site's script. The server
   keeps its own access log; that is the host's concern, not the site's.
4. **Release data is baked at build time, never fetched by the visitor's browser.** The
   workflow reads the newest hundred releases and, separately, `releases/latest` (so the last
   stable release is found however many candidates came after it) with `gh api` and hands the
   JSON to the build; the site shows the latest release, the latest release candidate and the
   last five entries with their dates and links to GitHub. On a merge to `main` the job waits
   for the candidate job, so the candidate that merge made is on the page. A call to `api.github.com` from the visitor's browser would hand
   their IP address to GitHub, which point 3 forbids. Without release data (a local build) the
   section links to the releases page.
5. **The release train publishes it.** `main.yml` gets a job `site`: on every merge to `main`
   (after the candidate job) and on every release tag (after `publish`) it builds the site,
   attaches `site-<version>.tar.gz` to the release the tag made (so every release carries the
   website that described it), and deploys by a `git push` to the web host's repository:
   clone, replace the site files with the build, commit `site: <ref> <commit>`, push. The
   host's repository holds every deployed version, so a rollback is a revert. Without the
   remote and the key the job only builds and uploads the artifact, so a fork and a pull
   request never deploy. The candidate job itself does not deploy.
6. **The deployment target lives in the forge, not in the application.** The remote, the
   host's key lines and the optional folder and branch are variables, the private key a
   secret, all in a GitHub environment `website` so only this job on `main` reads them. The
   key is a deploy-only key on the TransIP package; `docs/runbook.md` says how to rotate it
   and lists the host key fingerprints to check.
7. **The moving impression comes from the product itself.** Step 1 ships an animated tour as
   an HTML and CSS scene in the product's design language: the participant picks a time, the
   Webex meeting is made, the work item lands in the contact center queue, the agent joins.
   Step 3 adds a recorded walkthrough of the real interface: at every release tag a Playwright
   spec plays one story against the release's own code with the seeded demo (the participant
   books, confirms, moves, tries the video and joins; the team signs in, sees groups and
   staffing, sets the own queue, follows the agenda and picks the call up). The simulator stands
   in for Webex and the contact center in a context that is not recorded; the recording hides
   the test-environment bar and nothing else. The release carries `tour.webm`, a poster and
   captions in nl and en (`<track kind="captions">`), and a still of each main chapter
   (`tour-<chapter>.jpg`); the site shows the newest recording with its version and date and,
   under it, the stills as a gallery of the real screens captioned with the chapter's text, and
   says so plainly while there is none. The animation never plays
   when `prefers-reduced-motion: reduce` is set and the video never autoplays; both have a
   visible play and pause control, and the static poster is the fallback (WCAG 2.2.2, 2.3.1).
8. **The integrations are named, with Webex Meetings as the heart.** The site has an
   integrations section that draws the product in the middle and the systems around it:
   Webex Meetings (every appointment is a meeting; the participant joins from the browser),
   Webex Contact Center (the call becomes a work item in the team's queue, ADR 0018; "talk
   now", ADR 0019), Webex Messaging (alerts to administrators through a bot, ADR 0082),
   the organisation's identity provider (OIDC, ADR 0013 and 0083), email and SMS (ADR 0022),
   and the machine API for contact-center bots (ADR 0023). Every claim links to the ADR or the
   document that backs it; the site states what is built, not what is planned, unless marked
   "on the roadmap" (the Build status tables are the source).
9. **The roadmap is the open ADR steps on GitHub.** Every open row of a *Build status* table
   is an issue labelled `adr:NNNN` (ADR 0078). The build groups the open issues by decision,
   takes each title from `docs/adr/`, and links to the decision and to its open issues. No
   hand-written roadmap: closing an issue by merging the step takes it off the site at the
   next build.
10. **A standard is named only with its evidence and its state.** The section *Quality and
    standards* lists each standard with what it means, a state (`tested in CI`, `built in`,
    `partly`, `planned`) and a link into the repository. What is not done says so: the manual
    screen-reader audit, the penetration test, the listing on developer.overheid.nl, the TLS
    check of ADR 0086.
    A test fails when a repository link on the site points at a file that does not exist.
11. **Same bar as the product.** The site is checked for keyboard operation, `axe-core` with
   zero violations and Lighthouse accessibility ≥ 95 in both locales, added to the Playwright
   lane as step 2; `e2e/pages.json` gains the site's pages served from `site/dist/` by a static
   server. Until then the structural test of point 2 guards `lang`, `alt`, headings, labels
   and the CSP.

## Settings

None in the application: the website is outside it (point 6).

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `SITE_DEPLOY_REMOTE`, `SITE_DEPLOY_KNOWN_HOSTS`, `SITE_DEPLOY_DIR`, `SITE_DEPLOY_BRANCH` | B deployment (forge variables) | unset (build only); dir empty; branch `main` for an empty repository | dir relative, no `..` | repository admin | Environment `website` |
| `SITE_DEPLOY_KEY` | B deployment (forge secret) | unset (build only) | – | repository admin | Deploy-only key on the TransIP package |

**Fixed on purpose:** no cookies, no third-party requests, the CSP of point 3 (privacy by
design, not a tunable), the two locales `nl` and `en` (a third is a content PR), and the
accessibility targets of point 9.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #271, #285 | merged |
| 1 | `site/` (content nl/en, templates, stylesheet, script, animated tour in two tracks of six scenes, interactive demo, integrations, standards, roadmap from the ADR issues), `scripts/build_site.py`, `make site`, structural test, the `site` job with artifact and a git-push deploy to TransIP | #271, #285 | merged |
| 2 | The site's pages in the Playwright lane (keyboard, axe, Lighthouse) | | planned |
| 3 | Recorded walkthrough of the real interface at release time (`e2e/tour/`, `.github/workflows/tour.yml`, `.github/actions/e2e-stack`, `tour.webm`, poster, captions nl/en, the newest recording on the page) | #289 | merged; first recording on `v0.4.0-rc.86` (dispatched) |
| 3a | Stills of the main chapters as a gallery of the real screens, and the architecture plate (who uses it, the server, the systems around it, what goes where) in the enterprise section | #313 | in PR |
| 4 | TransIP: deploy key on the package, environment `website` with the remote, DNS for `openplanner.nl` and `www`, HTTPS and the response headers (HSTS, CSP, Referrer-Policy) in `.htaccess` (host work) | | planned |

## Consequences

- A new top-level directory `site/` and a build script; no dependency (Jinja2 and httpx are
  in the lock already; the build uses Jinja2 only).
- `main.yml` gains the job `site` with `contents: write` for the release asset and
  `issues: read` for the roadmap; the deploy step runs in the environment `website`.
- `docs/runbook.md` gains a section on the website: where it is hosted, how it is deployed,
  how to rotate the deploy key, how to roll back (a revert in the host's repository).
- `README.md` links to the website. `docs/architecture.md` does not change: the site is not
  part of the running system and reads nothing from it.
- Threat model: the site is static and takes no input, so no new abuse case; the deploy key
  is a new secret (`docs/threat-model.md`, secrets table).
- The product's texts stay the source for the words on the site: "participant", "agent" per
  ADR 0077; the site uses the same words for the people it addresses as the product does.
