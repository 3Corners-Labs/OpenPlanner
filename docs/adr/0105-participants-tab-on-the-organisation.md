# 0105. A Participants tab on the organisation

Status: accepted (2026-11-02), requested by the product owner. Amends ADR 0047 (admin
information architecture, §3 and §7) and ADR 0101 (sub-tabs). Built in the same pull request;
see *Build status*.

## Context

The second pass of the admin UX audit (`docs/ux-audit-admin.md`, findings O1 and O4) found that
what participants see and receive is spread over three places on an organisation: *Huisstijl*
(a tab), *Kanalen* (a tab) and *Teksten* (a sub-tab of *Overzicht*, between settings and login).
An administrator who wants to change how a participant experiences the service has no single
place to look, and *Overzicht*, which should be a summary, carries four unrelated sub-tabs.

Moving the three into one tab is not only a matter of labels. Each page has its own permission:
`branding.manage` (held back from custom roles, ADR 0039 B3), `integrations.manage` and
`texts.organisation.manage`. A tab that shows only with its own page's permission would hide
*Kanalen* or *Teksten* from an admin who may open them today. The portal's rule is that
navigation comes from the same matrix as the routes and never hides what a role may use.

## Decision

1. **The organisation gets a tab *Deelnemers* (*Participants*)** that holds what participants
   see and receive, as a second row: *Huisstijl* (the tab's own page), *Kanalen* and *Teksten*.
   The paths do not change (`/branding`, `/channels`, `/texts`), so no redirect is needed and
   bookmarks keep working.
2. ***Overzicht* keeps *Instellingen*, *Inbedding* and *Inloggen*.** *Teksten* moves out of it.
   The organisation page has five tabs (Overzicht, Deelnemers, Contact, Feestdagen,
   Noodsluiting), one fewer than before; ADR 0047 §7 is not exceeded.
3. **A tab whose pages have different permissions follows the matrix page by page.** The tab
   shows when the admin may open at least one of its pages; its link leads to the first page the
   matrix grants; its second row lists only the granted pages, and is left out when it would
   hold fewer than two. No permission is added, removed or changed.
4. **The label of a tab's own page may differ from the tab's** (`own_label_key`, as ADR 0101
   already has for entries): the tab is *Deelnemers*, its own page *Huisstijl*.
5. **The group is not changed here.** *Berichtteksten* and *Herinneringen* on a group raise the
   same question and are left for a later decision.

## Settings

None, after checking: the decision adds no threshold, limit, period, interval, toggle or
external address. Which tab holds which page is structure in the navigation register, not a
setting.

**Fixed on purpose:** the order of the tabs and sub-tabs (a design choice of the register).

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted | #424 | merged |
| 1 | `organisation.participants` in the register, `object_tabs` follows the matrix per page, `own_label_key` on tabs, the manual paths, `docs/admin-map.md`; tests in `tests/web/test_navigation.py` and `tests/admin/test_object_tabs.py` | #424 | merged |

## Consequences

- The manual topics for branding, channels and organisation texts name the new path;
  `docs/admin-map.md` is regenerated (CI compares it).
- `object_tabs` no longer requires a tab's own permission for the tab to show; an object tab
  without sub-tabs behaves as before.
- Authorisation is untouched: every route keeps its `require_permission`, the matrix sweep
  covers the same paths with the same results.
- A custom role with only `texts.organisation.manage` now sees a *Deelnemers* tab (leading to
  *Teksten*) instead of a *Teksten* sub-tab under *Overzicht*.
