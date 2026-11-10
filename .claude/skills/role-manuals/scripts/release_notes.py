"""What a release changed for each role: the input for the manuals' release notes, and the gate.

A version of the manuals describes one release. For readers it comes with a document that says
what is new for their role (``sources/90-wat-is-nieuw.md``). ``release.py notes`` collects the
commits between the last frozen version and the capture's release and uses this module to sort
them and to write ``evidence/release-notes.md`` (for the person who writes the document) and
``evidence/release-notes.json`` (for ``check.py`` and ``release.py freeze``).

This module needs no git and no workspace: it reads a Conventional Commit subject, sorts the
changes by the roles they touch, renders them, and judges the document against them. Nothing in
it decides what is new for a reader. It proposes, from two signals that are reliable in this
repository: a change to the help topic of a screen (``docs/manual/nl/<screen>.md``, which every
admin change must make) reaches the roles whose tasks use that screen, and a change to the
participant pages or the widget reaches the people who book. A change a reader can notice that
shows neither signal goes under *Nog te plaatsen*, never silently away.

The document names where each point comes from, in an HTML comment readers never see:
``<!-- bron: #644, #673 -->``, and what it leaves out on purpose:
``<!-- niet opgenomen: #583 alleen intern -->``. The gate rests on those:

* a number the release does not contain is an error (a point left over from another release);
* a new feature (``feat``) that is neither named nor left out blocks the freeze;
* any other change a reader can notice that is neither named nor left out is listed, not blocking;
* a line that still starts with ``> Invullen:`` blocks, so the template is never frozen as it is.
"""

import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace import RELEASE_NOTES, is_support  # noqa: E402

DOCUMENT = f"{RELEASE_NOTES}wat-is-nieuw"
"""The stem of the source that tells readers what is new; its prefix puts it last."""

RESULT_JSON = "evidence/release-notes.json"
RESULT_MD = "evidence/release-notes.md"
"""Where ``release.py notes`` writes its result, relative to the workspace."""

VISIBLE_TYPES = frozenset({"feat", "fix", "security", "perf"})
"""Conventional Commit types a reader can notice; the others (test, chore, ci, build, refactor,
style) change nothing on a screen, in an email or in an answer of the system. A ``docs`` commit
counts too when it changes a help topic, which readers see behind the (i) of a screen."""

TOPIC = re.compile(r"^docs/manual/nl/([\w-]+)\.md$")
PARTICIPANT_PATHS = ("app/api/templates/", "widget/")
"""The pages of the people who book and the widget: no help topic, so a path signals them."""

SUBJECT = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<text>.+?)(?:\s+\(#(?P<pr>\d+)\))?\s*$"
)
SOURCE = re.compile(r"<!--\s*(bron|niet opgenomen)\s*:(.*?)-->", re.DOTALL | re.IGNORECASE)
"""A comment of the document: ``bron:`` names the changes a point rests on, ``niet opgenomen:``
the ones it leaves out with a reason."""

IDENTIFIER = re.compile(r"#\d+|c:[0-9a-f]{7}")
"""A change is named by its pull request (``#644``), or by ``c:`` and its short hash when it has
none. The prefix keeps a word of a reason (``defaced``) from passing for a hash."""

PLACEHOLDER = re.compile(r"^>\s*Invullen:", re.IGNORECASE)
"""A line of the template that a person must replace."""


@dataclass(frozen=True)
class Change:
    """One commit of a release, as the notes use it.

    ``id`` is what the document cites. ``topics`` are the help topics the change touched and
    ``roles`` the roles that proposes (the roles of the tasks on those screens, and the people
    who book when a participant page changed); ``places`` says where in the manuals.
    """

    id: str
    type: str
    scope: str
    subject: str
    visible: bool
    topics: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    places: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Change":
        """Return the change ``as_json`` wrote."""
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            scope=str(data.get("scope", "")),
            subject=str(data.get("subject", "")),
            visible=bool(data["visible"]),
            topics=tuple(data.get("topics", ())),
            roles=tuple(data.get("roles", ())),
        )

    def as_json(self) -> dict[str, object]:
        """Return what ``check.py`` and ``release.py freeze`` read back."""
        return {
            "id": self.id,
            "type": self.type,
            "scope": self.scope,
            "subject": self.subject,
            "visible": self.visible,
            "topics": list(self.topics),
            "roles": list(self.roles),
        }


def parse_subject(subject: str) -> tuple[str, str, str, str]:
    """Return ``(type, scope, text, pull request number)`` of a Conventional Commit subject.

    A subject that is none (a revert, a merge) has the type ``other`` and keeps its text whole;
    a missing scope or number is "".
    """
    found = SUBJECT.match(subject.strip())
    if not found:
        return "other", "", subject.strip(), ""
    return (
        found["type"].lower(),
        found["scope"] or "",
        found["text"].strip(),
        found["pr"] or "",
    )


def classify(
    commits: Iterable[Mapping[str, Any]],
    topic_places: Mapping[str, Sequence[tuple[str, str]]],
    participant_role: str,
) -> list[Change]:
    """Sort commits into changes with the roles they touch, newest first as given.

    A commit is a mapping with ``hash``, ``subject`` and ``files``. ``topic_places`` maps a help
    topic to ``(role, place in the manuals)`` pairs, the tasks that use that screen.
    ``participant_role`` is the role of the people who book.
    """
    changes = []
    for commit in commits:
        kind, scope, text, number = parse_subject(str(commit["subject"]))
        files = [str(name) for name in commit.get("files", [])]
        topics = tuple(
            sorted({m.group(1) for name in files if (m := TOPIC.match(name)) is not None})
        )
        roles: dict[str, None] = {}
        places = []
        for topic in topics:
            for role, place in topic_places.get(topic, ()):
                roles.setdefault(role)
                places.append(f"{role}: {place}")
        if any(name.startswith(PARTICIPANT_PATHS) for name in files):
            roles.setdefault(participant_role)
        changes.append(
            Change(
                id=f"#{number}" if number else f"c:{str(commit['hash'])[:7]}",
                type=kind,
                scope=scope,
                subject=text,
                visible=kind in VISIBLE_TYPES or (kind == "docs" and bool(topics)),
                topics=topics,
                roles=tuple(roles),
                places=tuple(places),
            )
        )
    return changes


def render(
    changes: Sequence[Change], *, start: str, end: str, changelog: str, roles: Sequence[str]
) -> str:
    """Return ``evidence/release-notes.md``: the changes per role, in the order of ``roles``.

    ``start`` and ``end`` label the two releases, ``changelog`` is the project's own text for the
    release (may be empty). A change shows under every role it touches. The result is input for
    a person, in the manuals' language except for the subjects, which stay as committed.
    """
    seen = [c for c in changes if c.visible]
    technical = [c for c in changes if not c.visible]
    out = [
        "# Releasenotes als invoer voor de handleidingen",
        "",
        f"Van **{start}** naar **{end}**: {len(changes)} wijzigingen, waarvan {len(seen)} die "
        "een gebruiker kan merken (`feat`, `fix`, `security`, `perf`).",
        "",
        "Dit is invoer, geen tekst voor lezers. Schrijf in "
        f"`sources/{DOCUMENT}.md` per rol wat er voor die rol nieuw of anders is, in de taal "
        "van de handleiding. De indeling hieronder is een voorstel: een wijziging staat onder een "
        "rol als haar helponderwerp of een pagina van de deelnemers veranderde.",
        "",
        "Zet na elk punt, op een eigen regel, waar het vandaan komt: `<!-- bron: #123, #130 -->`. "
        "Laat je een wijziging bewust weg, zeg dat dan: `<!-- niet opgenomen: #123 reden -->`. "
        "Een nieuwe functie (`feat`) zonder een van beide blokkeert het vaststellen.",
    ]

    def lines(items: Iterable[Change]) -> list[str]:
        return [
            f"- **{c.type}**{f' ({c.scope})' if c.scope else ''}: {c.subject} ({c.id})"
            for c in items
        ]

    def with_tasks(items: Iterable[Change], role: str) -> list[str]:
        rows = []
        for change in items:
            rows += lines([change])
            here = [p.removeprefix(f"{role}: ") for p in change.places if p.startswith(f"{role}: ")]
            if here:
                rows.append(f"  - Taken: {'; '.join(here)}")
        return rows

    for role in roles:
        mine = [c for c in seen if role in c.roles]
        out += ["", f"## {role}", ""]
        out += with_tasks(mine, role) or ["Geen wijzigingen die voor deze rol zijn gevonden."]
    unplaced = [c for c in seen if not c.roles]
    out += ["", "## Nog te plaatsen", ""]
    out += [
        "Wijzigingen die een gebruiker kan merken en die bij geen taak of pagina van de "
        "deelnemers horen. Bepaal zelf voor welke rol ze tellen, of laat ze weg met een reden.",
        "",
        *(lines(unplaced) or ["Geen."]),
    ]
    out += ["", "## Technisch, niet voor de handleidingen", ""]
    by_type: dict[str, int] = {}
    for change in technical:
        by_type[change.type] = by_type.get(change.type, 0) + 1
    out += [
        f"{len(technical)} wijzigingen zonder zichtbaar gevolg: "
        + (", ".join(f"{n} {kind}" for kind, n in sorted(by_type.items())) or "geen")
        + ".",
        "",
        *lines(technical),
    ]
    out += ["", "## Uit CHANGELOG.md van het project", ""]
    out += [changelog.strip() or "Geen tekst voor deze release.", ""]
    return "\n".join(out)


@dataclass(frozen=True)
class Result:
    """The release notes of one range: the two commits and releases, and every change.

    The one reader and writer of ``evidence/release-notes.json``, so ``check.py`` and
    ``release.py`` ask the same questions of the same file. ``start`` and ``end`` are full commit
    hashes; the releases are the nearest release tags, or "".
    """

    start: str
    end: str
    start_release: str
    end_release: str
    changes: tuple[Change, ...]

    def as_json(self) -> str:
        """Return the file's text."""
        return (
            json.dumps(
                {
                    "from": self.start,
                    "from_release": self.start_release,
                    "to": self.end,
                    "to_release": self.end_release,
                    "changes": [c.as_json() for c in self.changes],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, text: str) -> "Result":
        """Return the result a file holds."""
        try:
            data = json.loads(text)
            return cls(
                str(data["from"]),
                str(data["to"]),
                str(data.get("from_release", "")),
                str(data.get("to_release", "")),
                tuple(Change.from_json(item) for item in data.get("changes", [])),
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"not a release notes file: {error!r}") from error

    def begins_at(self, baseline: str) -> bool:
        """Return whether the range starts at ``baseline``, a hash or an abbreviation of one."""
        return bool(baseline) and self.start.startswith(baseline)

    def covers(self, start: str, end: str) -> bool:
        """Return whether the range runs from the full hash ``start`` to ``end``."""
        return (self.start, self.end) == (start, end)


def topic_places(
    manuals: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[tuple[str, str]]], str]:
    """Return the tasks per help topic as (role, place) pairs, and the people who book.

    ``manuals`` are the entries of ``build/manuals.json``. The second result is the role of the
    manual whose tasks sit on participant pages; "Deelnemers" when no manual has one.
    """
    places: dict[str, list[tuple[str, str]]] = {}
    participants = ""
    for manual in manuals:
        role = str(manual.get("role", ""))
        for task in manual.get("tasks", []):
            screen = str(task.get("screen", ""))
            participants = participants or (role if screen.startswith("participant:") else "")
            where = f"{manual['file']}.md:{task['line']} › {task['title']}"
            places.setdefault(screen, []).append((role, where))
    return places, participants or "Deelnemers"


def heading_roles(manuals: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return the roles of the manuals a reader has, in file order; support sources have none."""
    roles: dict[str, None] = {}
    for manual in manuals:
        role = str(manual.get("role", ""))
        if role and not is_support(str(manual["file"])):
            roles.setdefault(role)
    return list(roles)


def identifiers(text: str) -> tuple[set[str], set[str], list[str]]:
    """Return what a document names, what it leaves out, and both in the order they appear.

    Only the leading run of identifiers of a comment counts; what follows is the reason.
    """
    named: set[str] = set()
    left_out: set[str] = set()
    order: list[str] = []
    for kind, body in SOURCE.findall(text):
        for token in re.split(r"[,\s]+", body.strip()):
            if not IDENTIFIER.fullmatch(token):
                break
            (named if kind.lower() == "bron" else left_out).add(token)
            order.append(token)
    return named, left_out, order


@dataclass
class Verdict:
    """What the document leaves open against the changes of its release."""

    unknown: list[str] = field(default_factory=list)
    unhandled_features: list[Change] = field(default_factory=list)
    unhandled_other: list[Change] = field(default_factory=list)
    placeholders: list[int] = field(default_factory=list)


def judge(changes: Sequence[Change], document: str) -> Verdict:
    """Judge the document against the changes of the release it is for."""
    known = {c.id for c in changes}
    named, left_out, order = identifiers(document)
    verdict = Verdict()
    verdict.unknown = sorted({token for token in order if token not in known})
    handled = named | left_out
    for change in changes:
        if not change.visible or change.id in handled:
            continue
        target = verdict.unhandled_features if change.type == "feat" else verdict.unhandled_other
        target.append(change)
    verdict.placeholders = [
        number
        for number, line in enumerate(document.splitlines(), start=1)
        if PLACEHOLDER.match(line)
    ]
    return verdict
