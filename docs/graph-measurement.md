# What the code graph saves: method and first numbers

Written for ADR 0127 (points 1 and 6). The Graphify MCP is worth using only where it saves
something a session would otherwise spend. This page says how to find out and holds what has been
measured, so a claim about the graph can be checked against a number.

## Method

Pick a fixed question about a symbol, answer it twice in a fresh session, and write down the size
of what each route put in front of the model.

| Route | What to record |
|---|---|
| Text | The output of the `grep` or file reads a session would run: lines, bytes, files |
| Graph | The size of the tool answer (`graphify_impact`, `graphify_callers`, `graphify_tests_for`), whether it was truncated, and the age in its `freshness` |

Bytes are a proxy for tokens (roughly four bytes to one). Also write down whether the answer
was enough: could the change be made from it, or did the session still open files? A smaller
answer that sends the session to read the files anyway saves nothing.

## First measurement (2026-11-15, index of commit `e983aa1`)

Question: what does a change to `Settings` (`app/config.py`) touch, and which tests cover it?

| Route | Size | Complete? |
|---|---|---|
| `grep -rn '\bSettings\b' app tests` | 960 lines, 86,867 bytes, 166 files | Yes, but every comment and docstring that says the word is in it |
| `graphify_impact` with `include_risk=true` | about 25,000 bytes, 60 nodes in 28 files plus 50 tests | No: the answer says `truncated: true` |

Reading: for a symbol this widely used the graph answer is about a third of the text answer and
names the functions, not the lines, but it stops at 60 nodes. Narrow questions (one function's
callers) are where the graph should win clearly; widely used symbols are where it truncates.
These are the first numbers, one question, one session. Add rows here when another is measured.

## Where the graph does not help

- Anything edited after the index commit: read the file.
- Strings, settings keys, templates and i18n keys: search the text.
- Whether the code is right: the graph's pull request review says "tests were not run" itself.
