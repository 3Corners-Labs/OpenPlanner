# 0105. Release tags come only from the release app

Status: accepted (2026-11-02, `po:approved` on #NNN), requested by the product owner. Amends
ADR 0079 (point 7: the release identity is a GitHub App of the organisation, not a personal
token) and ADR 0059 (the release-candidate tag is pushed by that app). Partly built; see
*Build status*.

## Context

A tag `vX.Y.Z` or `vX.Y.Z-rc.N` is what makes `main.yml` build, sign (keyless, ADR 0006) and
publish an image, and installations on the `rc` channel apply candidates on their own
(ADR 0059, docker01). `main.yml` refuses a tag outside `main` and a hand-made candidate, but on
a tag push it runs the workflow file *of the tagged commit*: whoever tags a commit of their own
branch also chooses the checks that judge it. Branch rules do not cover tags.

On 2026-11-02 a tag ruleset stopped moving and deleting `v*` tags, but not creating them:
anyone with write access can still push one. Restricting creation needs a bypass for the
workflows that tag, and GitHub does not accept *GitHub Actions* (`GITHUB_TOKEN`) as a bypass
actor; it accepts a GitHub App of the organisation.

The release workflow already needs an identity other than `GITHUB_TOKEN`, because a pull
request or tag made with that token starts no workflow (ADR 0079 point 7). The secret
`RELEASE_TOKEN` it asks for was never created, and a personal access token expires and acts as
a person.

## Decision

1. A GitHub App of the organisation, the *release app*, owned by 3Corners-Labs, installed on
   this repository only, with *Contents* and *Pull requests* read and write and nothing else.
   No webhook.
2. Its client ID (variable `RELEASE_APP_CLIENT_ID`) and private key (secret
   `RELEASE_APP_PRIVATE_KEY`) live in the environment `release`, whose deployment branches are
   `main` only. A job on any other branch cannot read them.
3. The jobs that create a tag or open the release pull request take a short-lived token from
   the app (`actions/create-github-app-token`, pinned by SHA) inside that environment: the
   release-candidate job of `main.yml`, and the `release-pr` and `tag` jobs of `release.yml`.
   A dry run of `release.yml` uses no environment and no app token, so it can still run on a
   feature branch. `RELEASE_TOKEN` goes away.
4. The tag ruleset `release tags` (`refs/tags/v*`) restricts creation, update and deletion,
   with two bypass actors: the release app, and the repository administrator role for a
   release by hand when the app is broken.
5. Because a tag pushed by the app starts workflows, `main.yml` listens to release tags only
   (`v*.*.*` without `-rc.`): a candidate is still built in the run that tags it, never twice.
6. Commits and tags made by the workflows carry the app's bot identity, so the history shows
   which identity made them.

## Settings

No application setting. The workflow reads two values from the forge, replacing
`RELEASE_TOKEN` of ADR 0079:

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `RELEASE_APP_CLIENT_ID` | A bootstrap (environment variable `release`) | none | the release app's client ID | repository admin | Needed before any release job runs; not secret. |
| `RELEASE_APP_PRIVATE_KEY` | A bootstrap (environment secret `release`) | none | the release app's private key (PEM) | repository admin | Never in the repository or on a laptop after it is stored; rotate by generating a new key in the app and deleting the old one. |

**Fixed on purpose:** the environment name `release`, its branch policy (`main` only) and the
app's permissions. Widening any of them is a decision, not a tuning knob.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed; environment `release` created with branch policy `main` (2026-11-02) | #NNN | in PR |
| 1 | The product owner creates the release app, installs it on this repository and stores its client ID and key in the environment `release` | GitHub setting | planned |
| 2 | `main.yml` (candidate tag through the app, tag trigger without `-rc.`) and `release.yml` (app token in `release-pr` and `tag`, `RELEASE_TOKEN` removed); tests; runbook, threat model, ADR 0079 table | #NNN | in PR |
| 3 | The tag ruleset restricts creation, with the release app and the administrator role as bypass; checked on the first candidate after the merge | GitHub setting | planned |

## Consequences

- Nobody with only write access can create, move or delete a release tag, so nobody can get
  an image signed and offered to installations without going through `main`.
- Releases no longer depend on a personal token that expires; the app key does not expire, so
  rotation is a deliberate act (runbook).
- The release-candidate job now depends on the app: if the app or its key is missing, no
  candidate is tagged after a merge (the images are still built and pushed by digest), and the
  job fails visibly. Order of work: step 1 before step 2 merges, step 3 after the first
  candidate succeeds.
- A pull request opened by the app is authored by a bot: the contributor-agreement check
  already skips bots, and the decision gate still needs an administrator's `po:approved`
  (ADR 0104).
