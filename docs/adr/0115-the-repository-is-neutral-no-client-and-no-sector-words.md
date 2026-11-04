# 0115. The repository is neutral: no client names and no sector words

Status: accepted (2026-11-04), decided by the product owner. Built: the rule, the guard test and
the rewritten history; the PR question and the CLAUDE.md rule are in the same change. Amends
the rule "ADRs are never rewritten" once (point 4); extends ADR 0077 (terminology).

## Context

OpenPlanner is a generic group-booking platform for any organisation. Its first client's name,
and words that tie it to one sector, had spread through code, tests, docs, ADRs, catalogue
texts, commit messages, branch names and tags. That is wrong for an open-source product, makes
the audit trail read as the story of one customer, and cannot be repaired once the first public
release (ADR 0108) carries it. Nobody ran the application in production yet, so the cost of
fixing it was lowest at the moment of the decision.

## Decision

1. **No client names and no sector words anywhere in the repository**: code, identifiers, tests,
   docs, ADRs, catalogue texts, scripts, commit messages, tags, branch names, pull request texts.
   The person who books and joins is the participant (*deelnemer*), as in ADR 0077. Examples and
   test data use neutral names (`acme`, `voorbeeld.nl`, `demo`, `balie`).
2. **Names of external standards and services are cited exactly** and stay: the Dutch state's
   certificate programme and API standards site, the *NL GOV* profile, and the names of other
   design systems. They are listed with their reason in `tests/test_terminology.py` (`ALLOWED`).
   The terms a custom form field may not use to ask for a national identity number are kept
   in Dutch as well, because Dutch customers write them in their own language
   (`app/domain/form_fields.py`; `EXEMPT_FILES`). In prose, the number is a *social security
   number*.
3. **A test fails on a violation**: `tests/test_terminology.py` scans every tracked text file
   and names the file and line. The words are written in pieces inside the test, so the
   repository does not contain them. A new entry in `ALLOWED` needs a reason in the same PR; the
   fix is otherwise the word. Review question 8 asks it of every pull request.
4. **The history was rewritten once, on 2026-11-04**, with the same rules (every blob, path,
   commit message, tag message and branch name), before the first public release. This is the
   one exception to "ADRs are never rewritten": the decisions are unchanged, only words. All
   commit hashes, tags and signatures before it changed; the images of earlier releases carry
   a commit that no longer exists. Nothing is rewritten again without a decision like this one.
5. **Persistent names follow.** Settings keys, event kinds, i18n keys, module and file names
   moved with the words (for example `participant_change_lead_minutes`, `waiting_left`, the
   widget element `<openplanner-booking>`). Migrations were rewritten with them; a database from
   before the rewrite does not fit and is recreated (only lab data existed).

## Settings

None.

**Fixed on purpose:** the list of words and allowed names lives in a test, not in a setting.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 1 | Rule, guard test, CLAUDE.md rule, review question 8, ADR | rewrite | built (2026-11-04, with the history rewrite) |
| 2 | Texts on GitHub that hold the old words (issues, PR bodies, release notes) | | follow-up |
| 3 | GitHub support asked to drop cached old commits and `refs/pull/*` | | follow-up |

## Consequences

- A fresh clone, the public source archive and the public images are neutral from 0.4.0.
- Anyone with an older clone, and the lab host, starts again from a new one.
- Texts outside git (issue and PR bodies, release notes) and GitHub's own caches keep the old
  words until steps 2 and 3.
- The guard has one more thing to maintain: the allowed names. It is short on purpose.
