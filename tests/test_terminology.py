"""The person who books and joins is a participant: the old words do not come back (ADR 0077).

Guards the places a reader sees: the texts of the catalogues, the templates, the stylesheets
and scripts of the pages, the widget, and the docs. Code, tests and earlier ADRs are not
scanned: renaming an identifier is a refactor, and ADRs are never rewritten.

A name that has to keep the old word is listed in ``KEPT`` with its reason. Anything else that
says "citizen" or "burger" fails this test; the fix is the word, not a new entry.
"""

import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OLD_WORDS = re.compile(r"citizen|burger", re.IGNORECASE)

KEPT = (
    # i18n keys are stable: only the texts change (ADR 0004, ADR 0077 point 3).
    r"\b(?:authz|admin|booking|call|queue|wait|join|direct|message|home|error)\.[\w.]*citizen[\w.]*",
    # The stored event value is in the database (ADR 0077, EventKind.CITIZEN_LEFT).
    r"citizen_left|CITIZEN_LEFT",
    # The name the audit log keeps for events written before the rename (ADR 0077 point 3a).
    r"citizen_change_lead_minutes",
    # The BSN is a fixed term of Dutch law, not a word for the person who books.
    r"burgerservicenummer|Burger Service Nummer|citizen service number",
    # A client's own slug: the example group of the docs and the demo seed.
    r"burgerzaken|Burgerzaken",
)
"""Tokens that may keep the old word; they are blanked before the search."""

KEPT_SENTENCES = (
    # The glossary names the old words on purpose, to say what they were.
    'earlier ADRs and `docs/HANDOVER.md` say "citizen" or "burger" for the participant.',
)
"""Whole sentences that may name the old words."""

EXEMPT_DOCS = ("docs/HANDOVER.md", "docs/adr-status.md")
"""Stakeholder history and the list of ADR titles keep the words they were written with."""

LAST_ADR_THAT_MAY_NAME_THEM = 82
"""ADRs up to this number were written before this guard and are never rewritten (CLAUDE.md);
0077 explains the rename. A newer ADR says participant."""

PAGE_FILES = (
    "app/**/*.html",
    "app/web/static/*.css",
    "app/web/static/*.js",
    "widget/src/*.js",
)
"""Templates, stylesheets and scripts of the pages, and the widget."""


def old_words_in(line: str) -> bool:
    """Return whether ``line`` still uses an old word outside the names that are kept."""
    if any(sentence in line for sentence in KEPT_SENTENCES):
        return False
    cleaned = line
    for pattern in KEPT:
        cleaned = re.sub(pattern, " ", cleaned)
    return OLD_WORDS.search(cleaned) is not None


def catalogue_texts(path: Path) -> Iterator[tuple[int, str]]:
    """Yield the line number and text of every ``msgstr`` line of a catalogue (keys are skipped)."""
    state = None
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if line.startswith("msgid"):
            state = "id"
        elif line.startswith("msgstr"):
            state = "str"
        elif line.startswith("#") or not line:
            state = None
        if state == "str":
            yield number, line


def plain_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield the line number and text of every line of a file."""
    yield from enumerate(path.read_text().splitlines(), start=1)


def guarded_files() -> Iterator[tuple[Path, bool]]:
    """Yield every guarded file with whether only its ``msgstr`` lines count."""
    for locale in sorted((ROOT / "app" / "i18n" / "locales").glob("*.po")):
        yield locale, True
    for pattern in PAGE_FILES:
        for path in sorted(ROOT.glob(pattern)):
            yield path, False
    for path in sorted((ROOT / "docs").glob("*.md")):
        if path.relative_to(ROOT).as_posix() not in EXEMPT_DOCS:
            yield path, False
    for path in sorted((ROOT / "docs" / "manual").rglob("*.md")):
        yield path, False
    for adr in sorted((ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")):
        if int(adr.name[:4]) > LAST_ADR_THAT_MAY_NAME_THEM:
            yield adr, False
    for name in ("CLAUDE.md", "CONTRIBUTING.md", "README.md", "SECURITY.md"):
        if (ROOT / name).exists():
            yield ROOT / name, False


def violations() -> list[str]:
    """Return ``file:line: text`` for every guarded line that still uses an old word."""
    found = []
    for path, only_texts in guarded_files():
        lines = catalogue_texts(path) if only_texts else plain_lines(path)
        found.extend(
            f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:100]}"
            for number, line in lines
            if old_words_in(line)
        )
    return found


def test_the_texts_and_docs_say_participant() -> None:
    assert violations() == [], "say participant / deelnemer (ADR 0077)"


def test_the_guard_sees_an_old_word() -> None:
    assert old_words_in("The citizen opens the page.")
    assert old_words_in("Burgers kunnen boeken.")
    assert old_words_in('msgstr "Welkom, burger"')


def test_the_guard_lets_the_kept_names_through() -> None:
    assert not old_words_in('{{ _("admin.agenda.citizen") }}')
    assert not old_words_in("EventKind.CITIZEN_LEFT stores citizen_left")
    assert not old_words_in("a citizen service number (BSN), burgerservicenummer")
    assert not old_words_in("`openplanner:group_admin:gem-a/burgerzaken`")
    assert not old_words_in(KEPT_SENTENCES[0])


def test_the_exempt_documents_exist() -> None:
    assert all((ROOT / name).exists() for name in EXEMPT_DOCS)


def test_the_kept_sentence_is_still_in_the_glossary() -> None:
    glossary = (ROOT / "docs" / "architecture.md").read_text()
    assert all(sentence in glossary for sentence in KEPT_SENTENCES)
