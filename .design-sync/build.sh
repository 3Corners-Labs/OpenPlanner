#!/bin/sh
# Builds the stylesheet /design-sync uploads: site.css then admin.css, in the order the app loads them.
set -eu
mkdir -p .design-sync/pkg/dist
cat app/web/static/site.css app/web/static/admin.css > .design-sync/pkg/dist/openplanner.css
