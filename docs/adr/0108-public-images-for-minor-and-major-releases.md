# 0108. Public images for minor and major releases only

Status: accepted (2026-11-02), requested by the product owner. Built: steps 1 and 3 in
#494; step 2, the administrator's act, is done (anonymous pull of 0.4.0 and 0.5.0 checked on 2026-11-08). Extends ADR 0006 (image
publishing and signing), ADR 0052 (releases), ADR 0059 (release candidates) and ADR 0078
(release audit).
Note (2026-11-05): ADR 0116 point 10 amends point 1: every `vX.Y.Z` release goes to the public
packages, release candidates stay private; the audit stays for `vX.Y.0`.

## Context

The repository is public: source, tags, pre-releases, release notes, SBOMs and CI logs can be
read by anyone. The images are not. `ghcr.io/3corners-labs/openplanner` and
`ghcr.io/3corners-labs/openplanner-proxy` are private packages, so their keyless cosign
signatures and SBOM attestations, which live next to the image in the same package, are
private too. An outsider (an auditor, a prospective client, a pentester) can read how an image
is built but cannot run `cosign verify` on it, nor tie a running image to a commit and an SBOM.

Two constraints shape the answer:

1. **Visibility on ghcr.io is per package, not per tag.** Making the existing packages public
   would publish every release candidate (one per merge, ADR 0059) and every patch release.
2. **Making a package public cannot be undone.** GitHub offers no way back to private, and
   there is no API to change visibility; an organisation administrator does it in the package
   settings.

The product owner wants outsiders to be able to audit what we release, but only the releases
we stand behind as a whole: the minor and major releases (0.3.0 → 0.4.0), which are also the
ones that carry the independent release audit of ADR 0078.

## Decision

1. **Two public packages, separate from the private ones.** `ghcr.io/<owner>/<repo>-public`
   and `ghcr.io/<owner>/<repo>-proxy-public` receive the images of a minor or major release
   (`vX.Y.0`) and nothing else. Release candidates and patch releases (`vX.Y.Z`, Z > 0) are
   never pushed there.
2. **The private packages stay as they are.** Every image, including `vX.Y.0`, is still
   pushed to the private packages; installations and the host updater keep pulling from there
   with their `read:packages` login (docs/install.md). Nothing changes on docker01.
3. **Same digest, same signature.** The release job pushes the `vX.Y.0` build to the private
   and the public package in one push (one digest), then signs and attests the SBOM on both
   references. No rebuild, no copy tool: `cosign copy` is deprecated in cosign 3 and the CI
   job `no deprecation warnings` would fail on it. `.github/actions/publish-image` gets an
   optional `public-image` input for this; `main.yml` sets it only when the tag matches
   `vX.Y.0`.
4. **The release says how to check it.** The release notes of a `vX.Y.0` release carry the
   public image references with their digests and the `cosign verify` and
   `cosign verify-attestation` commands, with the certificate identity of `main.yml` at that
   tag and the issuer `https://token.actions.githubusercontent.com`.
5. **One manual step, once.** After the first push creates the two public packages, an
   organisation administrator sets each to *Public* (Package settings → Change visibility),
   and links it to the repository. The release job checks anonymously that the pushed
   reference can be pulled and fails the release when it cannot, so a forgotten step is seen
   at the first release, not by an auditor.
6. **The first public release is 0.4.0.** Earlier releases are not backfilled: they predate
   the release audit of ADR 0078.

## Settings

None. This is release process in CI, not runtime behaviour.

**Fixed on purpose:** the rule "only `vX.Y.0`" lives in `main.yml`, not in a setting:
widening it publishes images irreversibly and is a decision (a PR touching `.github/`).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR proposed | #428 | merged |
| 1 | `public-image` input on `publish-image`; `main.yml` sets it for `vX.Y.0`; anonymous pull check; verify commands in the release notes | #494 | merged |
| 2 | Organisation administrator sets both public packages to Public (person) |  | built (2026-11-08: `openplanner-public` 0.4.0 and 0.5.0 pull without a login) |
| 3 | `SECURITY.md` and `docs/runbook.md`: how an outsider verifies a release | #494 | merged |

## Consequences

- An outsider can verify a minor or major release end to end: signature, SBOM attestation,
  commit, and the audit in its release PR.
- **A patch release is not public.** A security fix shipped as 0.4.1 is not in the public
  package until 0.5.0, so the public 0.4.0 shows a known, fixed weakness for that time. If
  that is unwanted, the rule becomes "`vX.Y.Z` releases, not candidates"; that is a new ADR.
- Release candidates stay private: they are untested on purpose and change many times a day.
- Every public image is public for good. A release that turns out to contain something that
  must not be public cannot be withdrawn from caches and mirrors; the release audit
  (ADR 0078) is the check before that point.
- The threat model gains an entry: public images make the exact dependency set of a release
  easier to study. The SBOMs are already public, so the added exposure is small.
