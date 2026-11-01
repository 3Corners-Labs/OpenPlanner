# 0099. Sub-tabs on a side-navigation entry, and Advanced settings in three

Status: accepted (2026-11-01), requested by the product owner. Extends ADR 0047 (admin
structure, §3 and §7) and ADR 0042 §11 (System status and Advanced settings). Built in this
PR; see *Build status*.

## Context

*Advanced settings* (`/admin/settings`) grew into one page with System status, the
certificate and its source, the data fields, the login test, the configuration package of
ADR 0089 and about twenty topics of settings. The product owner finds it too long: to reach
the configuration package you scroll past everything else, and the page gives no overview of
what is on it.

ADR 0047 gives sub-tabs only to the tabs of an object page (§3); a side-navigation entry is
one page, and `tests/web/test_navigation.py` refuses sub-tabs on it. A second side bar next to
the main one was considered and rejected: two vertical menus take width from the forms, do not
fit a phone, and are a pattern the portal does not have. The common advice (GOV.UK Design
System, NL Design System, Nielsen Norman Group) is tabs for a few tasks that differ in kind,
and a list of links "On this page" for one long page of items of the same kind.

## Decision

1. **A side-navigation entry may have sub-tabs**, declared in the navigation register like
   the sub-tabs of an object tab (ADR 0047 §3): a second row under the page header whose first
   item is the entry's own page. Each sub-tab is a page with its own path under the entry's
   path, its own permission (a sub-tab the matrix does not grant is not shown) and its own
   page width. The rule "changing the structure needs an ADR" (ADR 0047 §7) applies to a
   sub-tab on an entry as to one on a tab: adding one only needs review question 7.
2. **Advanced settings gets three sub-tabs**, one per task:
   - *System status* (`/admin/settings`, the entry's own page, still the target of the top
     bar's *Status*): the environment, the certificate and its source, what production would
     refuse, the class A and B variables, and the data fields;
   - *Settings* (`/admin/settings/values`): every class C and D setting by topic, and the
     login test of ADR 0042 §8 above the topics;
   - *Configuration package* (`/admin/settings/package`, `settings.platform.manage` only):
     export and import (ADR 0089).
3. **A long page of items of the same kind gets "On this page"**: a list of links to its
   headings at the top, without JavaScript. The *Settings* sub-tab is the first to have one;
   it lists every topic shown.
4. **A change lands on its own sub-tab.** Every form posts to the existing route; a refusal
   is shown on the sub-tab the form lives on, and a confirmation redirects to that sub-tab with
   the anchor of the changed part.
5. The manual topic *advanced-settings* covers all three sub-tabs.

## Settings

None. The sub-tabs and the list of links are structure, not tunable values.

**Fixed on purpose:** which sub-tabs exist and what they hold is the register's job (ADR
0047), not a setting.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR | this PR | in PR |
| 1 | Sub-tabs on an entry in the register, Advanced settings in three, "On this page" on *Settings*, manual | this PR | in PR |

## Consequences

- `docs/admin-map.md` shows the three sub-tabs under *Advanced settings*.
- Bookmarks of `/admin/settings#topic-…` land on *System status*; the topics are one click
  away on *Settings*.
- Pages are shorter, so a refused form re-renders close to the form, and the error summary
  pattern stays usable.
- Other long entries (for example *Integrations*) can get sub-tabs later under question 7.
