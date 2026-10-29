# design-sync notes — OpenPlanner

- **Tokens-only design system.** OpenPlanner has no React components. The only component, `<gov-booking>` (widget/), is a vanilla Web Component that loads all its text and availability from the widget API on its own origin, so in Claude Design it would only show "unavailable". It is deliberately left out (decided with the product owner, 2026-10-29).
- **Source of the styles:** `.design-sync/build.sh` (= `cfg.buildCmd`) concatenates `app/web/static/site.css` + `app/web/static/admin.css` (in the order the app loads them) into `.design-sync/pkg/dist/openplanner.css` (gitignored). `.design-sync/pkg/` is a sync-only stub package (`openplanner-styles`, empty `index.js`) so the converter has an entry; `cssEntry` must stay inside that package (converter bound).
- **Run from the worktree/repo root:** `./.design-sync/build.sh`, then the driver with `--node-modules .ds-sync/node_modules`. `[DTS_REACT]` and `[ZERO_MATCH]` are expected (no components).
- **Playwright:** the repo's e2e pin (1.64.1) needs chromium-1248, which was not cached; `playwright@1.62.0` matches the cached chromium-1234 and was installed in `.ds-sync/` only.
- **Verification** was visual, outside the converter (there are no cards): a citizen form page and an admin page (light and dark) with `styles.css` only, screenshotted with playwright. All rendered as in the app.
- **Fonts:** system font stack only, so no `[FONT_MISSING]`.

## Re-sync risks

- `.design-sync/conventions.md` lists class names and tokens by hand; a rename in site.css/admin.css makes it lie. Re-run the name check (grep each class/token in `ds-bundle/_ds_bundle.css`) on every re-sync.
- Organisation branding (`/o/{slug}/theme.css`, ADR 0041) is not synced; designs show the default NL Design System look.
- If a new stylesheet joins the app (beyond site.css + admin.css), add it to `build.sh`.
- `admin.css` uses `light-dark()`; dark mode depends on `data-theme` on the `.admin` root.
