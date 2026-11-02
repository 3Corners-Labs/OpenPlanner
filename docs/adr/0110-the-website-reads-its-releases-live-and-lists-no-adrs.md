# 0110. The website reads its releases live, lists no ADRs, and a missing deploy fails

Status: accepted (2026-11-02), decided by the product owner in the conversation of
2026-11-02. Supersedes ADR 0087 point 4 (release data only baked in) and point 9 (the roadmap
is the open ADR steps); amends ADR 0087 point 3 (one origin besides our own, for one request)
and point 5 (a deploy without its configuration fails on the real repository). Partly built:
steps 1 to 3 in this PR; step 4 is configuration in GitHub by a repository administrator.

## Context

On 2026-11-02 the product owner noticed two things on openplanner.nl.

- The release candidate on the page was `v0.4.0-rc.88` while GitHub had `v0.4.0-rc.168`. The
  page is rebuilt on every merge, but the deploy step skips itself with a notice when the
  environment `website` has no remote and no key, and that environment had neither: the site
  was deployed once by hand on 2026-11-01 and never again. The skip was invisible because the
  job stayed green.
- The roadmap section listed every decision with open steps by its ADR number and title, and
  every integration and standard linked as "ADR NNNN". The page is for organisations that may
  use the product; ADR numbers are the repository's working vocabulary, not theirs.

The product owner asked for the page to read GitHub itself, so a new release is on it without
a deploy, and for the deploy to be repaired as well, so text changes still go out.

## Decision

1. **The release section reads GitHub from the browser.** After the page has loaded, the
   site's script asks `https://api.github.com/repos/3Corners-Labs/OpenPlanner/releases` (the
   newest thirty) and `.../releases/latest` with `credentials: "omit"` and
   `referrerPolicy: "no-referrer"`, and replaces the latest release, the latest release
   candidate and the recent list with what it gets. It applies the same rules as the build:
   no drafts, only links under the repository's own releases page, newest first. Values go
   into the page as text only, never as HTML. The repository is public, so no token is
   involved.
2. **The baked list stays the fallback.** The build keeps baking the release data in (ADR
   0087 point 4's workflow), so the page is complete without JavaScript, when GitHub does not
   answer, or when a visitor has used up GitHub's anonymous limit of sixty requests an hour.
   The script changes nothing on any failure.
3. **We say what this costs.** The visitor's browser now tells GitHub its IP address when it
   loads the page; no cookie, no storage and no other origin is added. The CSP gains
   `connect-src https://api.github.com` and nothing else; the footer and the release section
   say that the browser asks GitHub for the release list and for nothing else. Scripts,
   styles, fonts, images and media stay `'self'`.
4. **The main page lists no ADRs.** The roadmap is a short hand-written list in
   `site/content/{nl,en}.json`, in the reader's words, with a link to all open work on
   GitHub. Each item carries the number of the decision it stands for in a field the page
   does not show; a test fails when that decision is built according to `docs/adr-status.md`,
   so a built item cannot stay on the roadmap. Links from integrations and standards name the
   document they open ("design decision", "architecture"), not an ADR number. The open-source
   section may still say that decisions are recorded in the repository. The build no longer
   reads the GitHub issues (`--issues` and the `issues: read` permission go).
5. **A deploy without its configuration fails on the real repository.** The `site` job tells
   `scripts/deploy-site.sh` whether it runs in `3Corners-Labs/OpenPlanner`; there, a missing
   remote, key or host key is an error, so the run turns red instead of green. A fork or a
   local run still builds and stops with a notice.
6. **A disclaimer says who is responsible.** The footer of both pages and a section of the
   README state that OpenPlanner is provided as it is, without warranty or liability (the
   EUPL-1.2, sections 7 and 8, already say so for the licence), that whoever installs and runs
   it is responsible for its configuration, hosting, use and compliance with the law, and that
   the documentation and the standards tables are no legal or compliance advice and no
   guarantee. The wording is the product owner's to have reviewed by a lawyer together with
   the CLA (ADR 0054); it is not legal advice either.

## Settings

None in the application. The deploy configuration stays as ADR 0087 lists it (forge variables
`SITE_DEPLOY_REMOTE`, `SITE_DEPLOY_KNOWN_HOSTS`, `SITE_DEPLOY_DIR`, `SITE_DEPLOY_BRANCH` and the
secret `SITE_DEPLOY_KEY` in the environment `website`).

**Fixed on purpose:** the GitHub address and the repository name in `site/static/site.js`
(the CSP names the same origin, and the page is about this one repository); the thirty
releases the script asks for (enough to find the newest candidate and five recent entries).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | this PR | in PR |
| 1 | Release section read live by `site.js`, CSP `connect-src`, texts nl/en, tests | this PR | in PR |
| 2 | Roadmap as a hand-written list, ADR numbers off the page, `--issues` removed, test against `docs/adr-status.md` | this PR | in PR |
| 3 | `SITE_DEPLOY_REQUIRED` in the `site` job; `deploy-site.sh` fails without its configuration on the real repository; test | this PR | in PR |
| 3a | Disclaimer in the footer (nl, en) and in the README | this PR | in PR |
| 4 | The deploy key and the variables in the environment `website` (ADR 0087 step 4; a repository administrator) | | planned |

## Consequences

- Until step 4 is done, every merge to `main` turns the main run red at the `site` job. That
  is the point: the gap is visible until it is closed.
- The page shows the newest release within seconds of its publication, also when a deploy is
  late; the text of the page still changes only by a deploy.
- `docs/threat-model.md` (the website row) and `docs/dataflow.md` mention the visitor's request
  to GitHub. `site/README.md` and the runbook's CSP line change with the CSP.
- The roadmap needs a hand when a new decision is worth showing; the test only removes, it
  never adds.
