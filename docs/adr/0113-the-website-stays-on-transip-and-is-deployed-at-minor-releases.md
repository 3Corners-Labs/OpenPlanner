# 0113. The website stays on TransIP and is deployed at minor and major releases

Status: accepted (2026-11-03), decided by the product owner in the conversation of
2026-11-03. Partly supersedes ADR 0111: the hosting by GitHub Pages is withdrawn; the rule that
only a minor or major release publishes stays. Restores ADR 0087 points 5 and 6 (deploy by
`git push` to TransIP with a key in the environment `website`) and ADR 0110 point 5 (a missing
deploy configuration fails), with the changes below. Partly built: steps 1 to 3a in this PR;
step 4 is configuration in GitHub and at TransIP by an administrator.

## Context

ADR 0111 (merged in #475, built nowhere live yet) moved openplanner.nl to GitHub Pages so that
no deploy key had to be kept. The product owner reversed that the same day: openplanner.nl
stays on the TransIP web hosting package it runs on today, and the site is updated as part of
the release, by the git path that made the first version. The DNS never moved, so nothing is
live on Pages and nothing has to be undone at the registrar.

The page reads its release list from GitHub in the visitor's browser (ADR 0110), so a deploy
is needed only when the text, the design or the recording changes.

## Decision

1. **TransIP serves the site, as under ADR 0087.** `scripts/deploy-site.sh` clones the package's
   git repository, replaces the site files with the build and pushes; TransIP AutoGit deploys
   the production branch. The script, its test, `site/deploy/autogit.yml` and
   `site/root/.htaccess` come back exactly as ADR 0087 had them. No GitHub Pages: the job
   `pages`, `site/root/CNAME` and the environment `github-pages` are gone.
2. **The deploy is a step of the release chain, not of every merge.** The job `site` builds on
   every merge to `main` and on every release tag, as before, and attaches the tarball to the
   release. A new job `deploy-site` runs only when the tag is `vX.Y.0` (the rule ADR 0111 and
   ADR 0108 use), in the environment `website`, and pushes the build the `site` job made (the
   same bytes, passed as an artifact). Merges and patch releases do not deploy.
3. **Patch releases do not deploy either (the default).** A patch changes the product, not the
   story; the release list on the page is live anyway (ADR 0110). A text fix between minor
   releases waits, or the administrator re-runs `deploy-site` of the last minor tag. Deploying
   at every release would mean a deploy a week for text that changes rarely, with a key
   exposed to every tag run; if the product owner wants it, the condition is one line.
4. **A missing configuration fails, on the tag.** In `3Corners-Labs/OpenPlanner` the script
   fails without `SITE_DEPLOY_REMOTE`, `SITE_DEPLOY_KEY` or `SITE_DEPLOY_KNOWN_HOSTS`
   (`SITE_DEPLOY_REQUIRED`, ADR 0110 point 5). That makes the minor release's run red, not
   every merge's, which was the noise ADR 0110 caused and ADR 0111 removed.
5. **The key is narrower than under ADR 0087.** The environment `website` accepts only tags
   `v*.*.*` (ADR 0107: tags come only from the release app), and only `deploy-site` runs in
   it; the `site` job, which runs on every merge and checks out and builds repository content,
   no longer has access. The guardrail baseline (ADR 0109) lists `website` limited to tags and
   no longer `github-pages`.
6. **Headers are the host's again.** `.htaccess` sets the CSP, referrer policy, nosniff and
   frame options; HSTS and the redirect to HTTPS follow once the certificate for openplanner.nl
   is in the TransIP panel (ADR 0087 step 4). The footer says the site is hosted by TransIP;
   the visitor's browser talks to TransIP for the page and to GitHub for the release list, no
   cookie, no storage.

7. **The site says what the README says.** "The README" is `README.md` on the repository's
   front page. What OpenPlanner is, what it does and the quick start on the site
   (`site/content/{nl,en}.json`) must match it at every release. Two checks hold this:
   `tests/scripts/test_site_matches_readme.py` fails when the README's quick start commands,
   its demo address or its installation guide are not on the site in both locales, so the
   commands cannot drift; and the release checklist in the runbook has the person who tags a
   minor or major release read the README's opening and *What it does today* against the
   site's hero, features and enterprise sections (prose cannot be tested), before the tag.
   A change to the README's quick start therefore needs the site texts in the same PR.

## Settings

None in the application. The deployment is configured in the GitHub environment `website`:
variables `SITE_DEPLOY_REMOTE`, `SITE_DEPLOY_KNOWN_HOSTS`, `SITE_DEPLOY_DIR`,
`SITE_DEPLOY_BRANCH` and the secret `SITE_DEPLOY_KEY` (as ADR 0087 lists them; they are the
forge's, not the application's, so no class A–D).

**Fixed on purpose:** the publish rule `vX.Y.0`, the hosting provider's repository layout
(`www/`, `.autogit.yml`).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #485 | merged |
| 1 | Deploy script, its test, AutoGit file and `.htaccess` restored; `pages` replaced by `deploy-site` at `vX.Y.0`; `CNAME` removed | #485 | merged |
| 2 | Runbook, threat model, data flow, footer text, `site/README.md`, guardrail baseline (`website` limited to tags) | #485 | merged |
| 3a | The README check: test and release checklist step (point 7) | #485 | merged |
| 4 | Environment `website` with tags `v*.*.*`, the deploy key pair, the variables, the key on the TransIP package, AutoGit and HTTPS in the TransIP panel, the environment `github-pages` and the Pages settings removed (an administrator) | | planned |

## Consequences

- The live site changes at the next minor release (v0.4.0) after step 3. If step 3 is not done
  by then, the run of that tag is red until it is, which is the intended signal.
- Until then openplanner.nl keeps the hand deploy of 2026-11-01.
- One secret in the forge again; it can reach only the one job, on a release tag.
- ADR 0111 is superseded in its hosting, publish-by-Pages and headers decisions; its
  minor-or-major rule lives on here.
