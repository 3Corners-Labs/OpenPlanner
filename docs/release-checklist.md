# Release checklist

The reusable plan for any version, in the order it is done. The mechanics (workflow inputs,
the release app, the laptop fallback, rollbacks) are in `docs/runbook.md`, *Releasing a
version*; the rules are ADR 0052 (versions), ADR 0059 (candidates), ADR 0078 (audit),
ADR 0079 (the workflow) and ADR 0108 (public images). This page is the list a person ticks;
copy the checklist into the release PR's conversation or an issue titled `Release vX.Y.Z`.
**Minor or major only** marks steps a patch release skips. Whoever does a step is in brackets.

## A. Freeze and inventory (a day or more before)

- [ ] Agree the version and the date; announce a freeze of feature merges. (PO)
- [ ] `gh pr list --state open`: every PR is merged, closed or named below as *not in this
      release*. Known standing ones: Renovate updates (merge the green ones, a major as its own PR).
- [ ] `gh issue list --state open`: read every `adr:NNNN` issue. A step that the release
      promises is built; the rest is named in `CHANGELOG.md` or the README's *On the roadmap*.
      `uv run python -m scripts.adr_issues --counts` and `docs/adr-status.md` agree with the code.
- [ ] Issue *Guardrail drift* is closed: settings equal `.github/guardrails.json` or the
      baseline was changed in a decision PR (ADR 0109).
- [ ] Latest release candidate on `main` is green, runs on the test host (docker01), and the
      walk-through of `docs/runbook.md` (booking, confirm, join, cancel, closure, update) was done
      on it, by a person.
- [ ] **Minor or major:** ADR 0108 is built (public images on every release), both `-public` packages
      are set to *Public* by an organisation administrator after the first push, and the website
      deploy of ADR 0113 has its environment `website` (key, variables, TransIP AutoGit).
- [ ] Settings that were added are in the ADRs' *Settings* sections; `docs/settings.md`,
      `docs/authorization.md`, `docs/admin-map.md` are regenerated and CI-clean.
- [ ] A migration of this release was applied from the previous release's database
      (up on a copy of docker01's data; down on a fresh one is in CI).

## B. Documentation (same PR as the release, or earlier)

- [ ] `CHANGELOG.md`: `## Unreleased` reads as release notes; nothing is listed that is not built.
- [ ] `README.md`: opening, *What it does today*, *On the roadmap*, *Status*, quick start.
- [ ] `docs/try-it.md`, `docs/install.md`, `docs/runbook.md`: version numbers in examples
      (`git clone --branch vX.Y.Z`), steps still true. Run `./scripts/try.sh reset && ./scripts/try.sh`
      on a clean machine (macOS or Linux, and once with `try.ps1` on Windows) and click through.
- [ ] `docs/manual/{nl,en}/`: a topic for every admin screen added or changed.
- [ ] `docs/architecture.md`, `docs/dataflow.md`, `docs/threat-model.md`: diagrams and abuse
      cases for every changed flow.
- [ ] **Website (openplanner.nl, minor or major)**: `site/content/{nl,en}.json` against the
      README (hero, features, enterprise, roadmap, quick start; `tests/scripts/test_site_matches_readme.py`
      holds the commands). `python -m scripts.build_site` locally, open `site/dist`, check both
      languages. The site goes live with the tag (ADR 0113): fix it in a PR before step C.

## C. The release PR (Release workflow, ADR 0079)

1. [ ] *Actions → release → Run workflow* on `main`, `step = release-pr`, first with `dry_run`.
       Then for real. Leave `version` empty unless the number must be higher.
2. [ ] **Minor or major:** read `## Release audit`. P1 findings: fix in their own PRs and re-run
       the *release audit* job; P2: open issues. Then change the last line to `Verdict: pass`. (PO)
3. [ ] Set the label `po:approved` as an administrator; wait for green CI; the merge queue merges. (PO)

## D. Tag and publish

1. [ ] *Run workflow* with `step = tag`, `version = X.Y.Z`. Never "Draft a new release".
2. [ ] Watch `main.yml` for the tag: tag check, ZAP, mutation, build, push, sign, anonymous pull
       of the public images. For `vX.Y.0` also the `deploy-site` job.
3. [ ] The GitHub release has `sbom.cdx.json`, `sbom-proxy.cdx.json`, and the public image
       references with `cosign verify` commands.
4. [ ] Delete the candidates of the released version (command in the runbook).

## E. After the tag

- [ ] Test host: *Running now* shows `X.Y.Z` on `/admin/updates` (not a candidate); health green.
- [ ] A clean install of the release on a fresh Ubuntu or Debian VM with `scripts/install.sh --mode test`
      (ADR 0050), and the evaluation path of `docs/try-it.md` from the release's source archive.
- [ ] `cosign verify` of the image from outside the organisation (minor or major).
- [ ] openplanner.nl shows the new text and the release in its list; both languages, HTTPS, headers.
- [ ] Production installations: announce, superuser updates from `/admin/updates`; if the release
      changes the host side (installer, updater, compose), run `scripts/install.sh` first (runbook).
- [ ] ADR *Build status* tables and `docs/adr-status.md` mention the release; close the
      `Release vX.Y.Z` issue with the link to the release.

## If something is wrong

Never move or reuse a published tag: fix forward with the next patch (ADR 0052). A wrong tag
that was *not* published: runbook, *When the tag was wrong*. A bad image already pulled:
installations roll back within 24 hours from `/admin/updates`.
