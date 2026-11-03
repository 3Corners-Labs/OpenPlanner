# 0111. The website is served by GitHub Pages and published at minor and major releases

Status: accepted (2026-11-03), decided by the product owner in the conversation of
2026-11-03. Supersedes ADR 0087 points 5 and 6 (deploy by `git push` to TransIP with a key in
the environment `website`) and ADR 0110 point 5 (a missing deploy configuration fails). Partly
built: steps 1 and 2 in #475; step 3 is configuration in GitHub and DNS by an administrator.

## Context

ADR 0087 hosted openplanner.nl on a TransIP web hosting package and deployed every merge by a
`git push` with a deploy key held in the GitHub environment `website`. The key was never put
there, so the site was deployed once by hand (2026-11-01) and then stood still; ADR 0110 made
that gap a red run on every merge.

Since ADR 0110 the page reads the release list from GitHub in the visitor's browser, so a new
release no longer needs a deploy to appear. The text of the page changes rarely. The product
owner asked for a looser coupling: no key to keep, and the site published from the release
process, at a minor or major release such as 0.3.0 to 0.4.0. The repository is public; the
site holds nothing that is not already in it.

## Decision

1. **GitHub Pages serves the site.** The job `site` builds it on every merge and every release
   tag, as before; a new job `pages` publishes the build with `actions/deploy-pages`. No key,
   no secret and no variable: the deployment proves itself with the workflow's own token.
2. **Only a minor or major release publishes.** `pages` runs when the tag is `vX.Y.0` (the
   same rule ADR 0108 uses for the public images). Candidates, patch releases and merges build
   and keep the artifact; the live site does not change. The environment `github-pages`
   accepts only tags `v*.*.*`, so a branch can never publish, even by mistake in the workflow.
3. **The TransIP path goes.** `scripts/deploy-site.sh`, its test, `site/deploy/autogit.yml`
   and `site/root/.htaccess` are removed; the environment `website` is deleted and the
   guardrail baseline (ADR 0109) lists `github-pages` instead.
4. **Headers are the page's own, or GitHub's.** Pages takes no server configuration. The CSP
   and the referrer policy stay meta tags in the page; `frame-ancestors`, HSTS and
   `X-Content-Type-Options` are what GitHub sends. The page is static, takes no input and has
   nothing worth framing, so this is accepted.
5. **The visitor's browser talks to GitHub twice.** Once for the page (the host), once for the
   release list (ADR 0110). No cookie, no storage, no other party. The footer says the site is
   hosted on GitHub Pages.

## Settings

None in the application. The deployment needs no setting: the domain lives in
`site/root/CNAME` and in the repository's Pages settings.

**Fixed on purpose:** the publish rule `vX.Y.0` (a release decision, not a tunable).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | #475 | in PR |
| 1 | Job `pages`, the `vX.Y.0` rule, `upload-pages-artifact` in `site`, `CNAME`; TransIP script, test, AutoGit file and `.htaccess` removed | #475 | in PR |
| 2 | Runbook, threat model, data flow, footer text, guardrail baseline | #475 | in PR |
| 3 | Pages source *GitHub Actions* and the custom domain, environment `github-pages` limited to tags, environment `website` deleted, DNS of openplanner.nl to GitHub Pages, domain verified for the organisation (an administrator) | | planned |

## Consequences

- The live site changes at the next minor release after step 3 (v0.4.0), not before. Until
  then openplanner.nl stays on TransIP with the hand deploy of 2026-11-01, and its release list
  is already live through ADR 0110.
- A text fix between releases waits for the next minor release. That is the product owner's
  choice: fewer moving parts over faster text changes.
- One fewer secret in the forge and one fewer host to keep: the TransIP package can be ended
  once the DNS points at GitHub.
