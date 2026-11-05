---
name: pre-pr-review
description: Run before opening any PR in this repo. Runs the repo skills `security-review` and `code-quality-review` in parallel subagents and a correctness pass with `/code-review`, checks and deduplicates their findings, and writes one section for the PR description. Use before any `gh pr create`, or when asked to "review this branch" or "review before the PR".
---

# Pre-PR review (repo skill)

One command for the review CLAUDE.md asks for before every PR. Each concern has its own skill;
this one runs them on the same scope and merges what they find. It reports and changes no files.

## 1. Scope

- Default: the current branch against `main` (`git diff main...HEAD`) plus uncommitted
  changes. When a PR number or another branch is named, review that instead.
- Collect once: the base and head commits, `git diff --stat`, and the list of changed files.
  Hand the reviewers this scope and the file paths, not file contents: they read the files
  themselves.
- Stop when the diff is empty, and say so.

## 2. Run the reviewers

In one message, start two background subagents (`Agent` with `subagent_type:
"general-purpose"`). Give both the same scope and tell them not to edit any file.

- **Security:** "Read `.claude/skills/security-review/SKILL.md` and follow it for this scope.
  Reply with its Output section only."
- **Code quality:** "Read `.claude/skills/code-quality-review/SKILL.md` and follow it for this
  scope. Reply with its Output section only."

While they run, do the correctness pass in this thread with the built-in `code-review` skill at
level `high` on the same scope; it starts subagents of its own, so it cannot run inside one. When
`code-review` is not available, use gstack's `/review`.

## 3. Merge

When all three are back:

1. Check every finding before you keep it: open its file:line and confirm the claim. Drop what
   does not hold, and give the count of dropped findings.
2. Deduplicate across the reviewers. A finding that two of them raised on their own goes first.
3. Where the reviewers disagree, decide from your own reading and give the reason in one line.
4. Keep the security section in the form `security-review` writes it: the PR template and
   CLAUDE.md ask for its answers to the review questions.
5. Run `git status`: the review must not have changed a file.

High findings block the PR. Fix them, then rerun only the reviewer that raised them. Medium and
low findings go into the description, each with its fix or the reason to leave it.

## Output

Sections ready to paste into the PR description:

```
### Security review
(the security-review output: Q1–Q8, settings, rule checks, change impact, abuse cases, findings)

### Code quality review
(the code-quality-review output)

### Correctness
**Verdict:** ... and the findings that survived checking: numbered, severity, file:line

### Before merge
The high and medium findings of all three, deduplicated, numbered, each with its reviewer(s);
or "nothing open". Then: "<N> findings dropped after checking".
```
