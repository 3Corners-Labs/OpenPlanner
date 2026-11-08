#!/usr/bin/env bash
# Print the part of the release notes that tells an outsider how to verify the public images
# of a release (ADR 0108 point 4; every release since ADR 0116 point 10). Only a minor or major
# release (vX.Y.0) carries the independent audit of ADR 0078, so only its notes point to it.
#
#   scripts/public-release-notes.sh OWNER/REPO TAG APP_REFERENCE PROXY_REFERENCE
#
# The references carry the digest (ghcr.io/owner/repo-public@sha256:...). The certificate
# identity is the workflow file at that tag, which is what keyless signing recorded.
set -Eeuo pipefail

[ "$#" -eq 4 ] || { echo "usage: public-release-notes.sh OWNER/REPO TAG APP_REF PROXY_REF" >&2; exit 2; }
repository=$1 tag=$2 app=$3 proxy=$4
identity="https://github.com/${repository}/.github/workflows/main.yml@refs/tags/${tag}"
issuer="https://token.actions.githubusercontent.com"

cat <<TXT
## Verify the images

This release is also published in the public packages. Anyone can check which commit an image
was built from, and its software bill of materials, without an account:

| Image | Reference |
|---|---|
| Application | \`${app}\` |
| HTTPS proxy | \`${proxy}\` |

\`\`\`sh
for image in ${app} ${proxy}; do
  cosign verify "\$image" \\
    --certificate-identity ${identity} \\
    --certificate-oidc-issuer ${issuer}
  cosign verify-attestation "\$image" --type cyclonedx \\
    --certificate-identity ${identity} \\
    --certificate-oidc-issuer ${issuer}
done
\`\`\`

TXT
if [[ $tag =~ ^v[0-9]+\.[0-9]+\.0$ ]]; then
  printf '%s\n\n' "The independent audit of this release is in its release pull request, under *Release audit*."
fi
