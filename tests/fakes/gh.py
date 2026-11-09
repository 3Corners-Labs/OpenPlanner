"""The ``gh`` CLI behind the issue scripts (ADR 0078, 0109): canned reads, every call recorded.

``scripts.report_issue.IssueTracker`` and its subclass ``scripts.adr_issues.GitHub`` run ``gh``
through a ``Runner``; ``FakeGh`` is that runner, so no test talks to GitHub.
"""

import json
from collections.abc import Mapping, Sequence

from scripts.adr_issues import Issue

PR_STATES = {1: "MERGED", 2: "OPEN", 3: "CLOSED"}
"""The pull requests the fake GitHub knows, by number."""


def pr_state(number: int) -> str:
    """Return the state of pull request ``number``; ``UNKNOWN`` for one the fake does not know."""
    return PR_STATES.get(number, "UNKNOWN")


class FakeGh:
    """Stands in for ``gh``: answers the reads from canned data and records every call.

    ``label list`` and ``issue list`` answer from ``labels`` and ``issues``, ``issue create``
    prints the address of issue 42, ``pr view`` the state ``pr_state`` gives and
    ``api repos/<owner>/<repo>/pulls/<n>`` the pull request ``pulls`` holds under ``n``; every
    other call, a write, prints nothing.
    """

    def __init__(
        self,
        labels: Sequence[str] = (),
        issues: Sequence[Issue] = (),
        pulls: Mapping[int, Mapping[str, object]] | None = None,
    ) -> None:
        self.labels = list(labels)
        self.issues = list(issues)
        self.pulls = dict(pulls or {})
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        match list(args[:2]):
            case ["label", "list"]:
                return json.dumps([{"name": n} for n in self.labels])
            case ["issue", "list"]:
                return json.dumps(
                    [
                        {
                            "number": i.number,
                            "title": i.title,
                            "state": i.state,
                            "labels": [{"name": n} for n in i.labels],
                        }
                        for i in self.issues
                    ]
                )
            case ["issue", "create"]:
                return "https://github.com/o/r/issues/42\n"
            case ["pr", "view"]:
                return json.dumps({"state": pr_state(int(args[2]))})
            case ["api", path] if "/pulls/" in path:
                return json.dumps(self.pulls[int(path.rsplit("/", 1)[1])])
        return ""
