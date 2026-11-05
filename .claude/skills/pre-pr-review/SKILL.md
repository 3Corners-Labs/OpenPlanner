---
name: pre-pr-review
description: Run before opening any PR in this repo. Runs the repo skills `security-review` and `code-quality-review` in parallel subagents and a correctness pass with the built-in `/code-review`, checks and deduplicates their findings, and writes the review sections of the PR description. Use before any `gh pr create`, or when asked to "review this branch" or "review before the PR".
---

# Pre-PR review (repo skill)

One command for the review CLAUDE.md asks for before every PR. Each concern has its own skill;
this one fixes the scope, runs them, and merges what they find. The review itself changes no
file and no git state.

## 1. Scope

This is the one definition of the scope. `security-review` and `code-quality-review` use it when
they run on their own.

- Run `git fetch origin`, then take `origin/main...HEAD`: the branch since it left `main`. Never
  the local `main`; worktrees share it and it goes stale.
- Add the working tree: `git diff HEAD` for uncommitted changes, and
  `git ls-files --others --exclude-standard` for new files that are not staged yet. Those files
  are read in full.
- For a PR number, use `gh pr diff <number>` and the base and head from
  `gh pr view <number> --json baseRefOid,headRefOid`; for another branch, `origin/main...<branch>`.
- Collect once: the base and head commits, `git diff --stat`, and the list of changed and new
  files. Hand the reviewers this scope and the paths, not file contents: they read the files
  themselves.
- Stop when the scope is empty, and say so.

## 2. Run the reviewers

In one message, start two background subagents (`Agent` with
`subagent_type: "general-purpose"`). Give both the same scope, and tell them not to edit, stage
or commit anything, and not to run a command that writes.

- **Security:** "Read `.claude/skills/security-review/SKILL.md` and follow it for this scope.
  Reply with its Output section only."
- **Code quality:** "Read `.claude/skills/code-quality-review/SKILL.md` and follow it for this
  scope. Reply with its Output section only."

While they run, do the correctness pass in this thread with the built-in `code-review` skill at
level `high` on the same scope, so that its findings come back to you in full. When `code-review`
is not available, write "Correctness: not run" instead of using a reviewer that edits files.

## 3. Merge

When all three are back:

1. Check every finding before you keep it: open its file:line and confirm the claim. List each
   finding you drop, with a one-line reason.
2. Deduplicate across the reviewers. A finding that two of them raised on their own goes first.
3. Where the reviewers disagree, decide from your own reading and give the reason in one line.
4. Run `git status`: the review must not have changed a file or the index.

## 4. After the review

- Fix each high finding, or keep it with its reason in the description. A kept high from
  `security-review` needs the ADR or the product owner's instruction that decides it.
- Medium and low findings go into the description, each with its fix or the reason to leave it.
- After a fix, rerun every reviewer whose section cites a changed file, and always
  `security-review` when code changed after it ran, so that the sections describe the head the
  PR opens with.

## Output

Sections that follow `.github/pull_request_template.md`:

```
## Review questions
(security-review's answers to the template's questions)

## Security review
(the rest of security-review's output)

## Code quality review
(code-quality-review's output)

## Correctness
**Verdict:** ... and the findings that survived checking: numbered, severity, file:line

## Before merge
What stays open from all three, deduplicated and numbered, each with its reviewer(s) and its
fix or reason; or "nothing open". Then each dropped finding, one line with the reason.
```
