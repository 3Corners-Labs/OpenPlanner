# 0054. OpenPlanner is EUPL-1.2, contributions under a contributor licence agreement

Status: proposed (2026-10-29), requested by the product owner.

## Context

The repository is private and has no licence, so nobody but 3Corners may use it. The product
owner wants to publish it so that others can take part in its development. Wishes, in order:

1. Others co-develop: improvements by users, integrators and hosters come back to one
   codebase instead of staying in private forks.
2. Nobody takes the code closed: a hosted or modified OpenPlanner keeps its source public.
3. 3Corners keeps the name and the option to sell a commercial licence or support.

The project is not primarily government-driven, but its first users are government contact
centres, which prefer (and in the Netherlands are told to prefer) an OSI-approved licence.

Options weighed:

| Licence | Co-development | Hosting as a service | OSI open source | Fit |
|---|---|---|---|---|
| MIT / Apache-2.0 | Contributions optional, private forks common | Allowed, changes may stay closed | Yes | Wish 2 fails |
| AGPL-3.0 | Strong: changes shared, also when hosted | Allowed, changes published | Yes | Good; US-drafted, on some "no" lists |
| **EUPL-1.2** | Strong: changes shared, also when hosted (art. 1) | Allowed, changes published | Yes | **Chosen** |
| BSL 1.1, PolyForm Noncommercial | Weak: contributors give free work to one vendor | Only with permission | No | Wish 1 fails |

No open-source licence forbids commercial use or hosting; that would need a source-available
licence, which works against wish 1. Wish 3 is met by the trademark and the contributor
agreement instead.

## Decision

1. **Licence.** Every file in the repository is licensed under the European Union Public
   Licence v1.2 only (`EUPL-1.2`, not "or later"), copyright 3Corners and the OpenPlanner
   contributors. The official English text is in `LICENSE` and `LICENSES/EUPL-1.2.txt`,
   unchanged. "Only" keeps a later EUPL version, published by the European Commission, from
   applying without a decision of ours.
2. **Why EUPL over AGPL.** Same effect on hosted modifications (article 1 counts "providing
   on-line access to its essential functionalities" as distribution), plus: official texts in
   all EU languages including Dutch, Dutch law and Dutch courts for a Dutch licensor
   (articles 14 and 15), and the licence Dutch and EU public bodies are asked to prefer.
3. **REUSE.** Licence and copyright of every file are declared in `REUSE.toml` (REUSE
   specification 3.3), not in a header per file. A file copied from elsewhere gets its own
   annotation with its own copyright and licence. CI runs `reuse lint` and fails on a file
   without licence information.
4. **Contributor licence agreement.** Every contribution from outside 3Corners is made under
   `docs/cla.md`: the contributor keeps the copyright and grants 3Corners a perpetual,
   non-exclusive licence including the right to sublicense and relicense, plus a patent
   licence. In return 3Corners promises that every contribution stays available under
   EUPL-1.2 or another OSI-approved licence. A pull request states agreement with a checkbox
   in its description; CI fails a pull request from a non-member without it. A Developer
   Certificate of Origin was rejected: it cannot support a commercial licence for code
   others wrote.
5. **Name.** The licence grants no rights to the name or logo (article 5). "OpenPlanner" and
   "3Corners" are trademarks of 3Corners; a fork must carry another name. The README says so.
6. **Dependencies.** The allow-list for dependencies (MIT, BSD, Apache-2.0, PSF) is
   unchanged; all of them can be combined with EUPL-1.2. Tools that only run in CI as a
   container (the REUSE linter, GPL-3.0) are not dependencies of the product.

## Settings

None. A licence is not a setting: it applies to the code, not to a deployment.

**Fixed on purpose:** the licence identifier and the licence text, which must stay verbatim.

## Consequences

- The repository can be made public. Before that: the secret scan (gitleaks over every
  branch, clean on 2026-10-29), a read of issues, pull request texts and Actions logs, and a
  check that `docs/HANDOVER.md` holds nothing confidential about clients.
- Anyone may use, host and sell OpenPlanner; whoever distributes or hosts a modified version
  publishes its source under EUPL-1.2 or a licence of its compatibility list. That list
  includes weaker copyleft licences (MPL-2.0, LGPL), so a combined work can end up under one;
  this is the known cost of EUPL.
- Code that only talks to OpenPlanner through its interfaces (an agent bot via the machine
  API, a website embedding the widget) is not a derivative and keeps its own licence.
- Outside contributors sign the agreement once per pull request with a checkbox, which is
  some friction. The CLA text and the trademark claim need review by a lawyer before the
  repository goes public.
- Not done here: `publiccode.yml` (for listing on developer.overheid.nl) and registering the
  trademark.
