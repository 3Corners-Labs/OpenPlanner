"""The repository is neutral: no client names and no sector words (ADR 0077, ADR 0115).

OpenPlanner is a generic booking platform. Its code, tests, docs, ADRs, catalogue texts and
commit messages name no client and no sector. The person who books and joins is the participant
(deelnemer). The words below are written in pieces so that this file does not contain them.

Names of external standards and services that are cited exactly stay in ``ALLOWED``, each with
its reason. A new entry needs a reason, the fix is otherwise the word.

Office files (the Word version of the architecture plates) are zip archives of XML; their text
is read part by part with ``zipfile``. Images and PDFs are not read: the PNG and PDF plates are
built from the same source as the Word file, ``scripts/diagrams/content.py``.
"""

import html
import re
import shutil
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _w(*parts: str) -> str:
    return "".join(parts)


SECTOR = re.compile(
    "|".join(
        [
            _w("citi", "zen"),
            _w("bur", "ger"),
            _w("over", "heid"),
            _w("govern", "ment"),
            _w("gem", "eente"),
            _w("minis", "terie"),
            _w(r"\bg", r"ov\b"),
            _w(r"g", "ov-booking"),
            _w("belas", "ting"),
            _w("fin", "[-_ ]?bld"),
            _w("rij", "ks"),
            _w("toe", "slag"),
            _w("inwo", "ner"),
            _w("vergun", "ning"),
            _w("aan", "gifte"),
        ]
    ),
    re.IGNORECASE,
)
"""The words that do not appear anywhere in the repository (client names, sector words)."""

ALLOWED = (
    # The certificate programme and the API standards site of the Dutch state, cited as they are.
    _w("pkio", "verheid"),
    _w("developer.over", "heid.nl"),
    # Names of external design systems and profiles, cited as references (ADR 0041, ADR 0083).
    _w("NL G", "OV"),
    _w("G", "OV.UK"),
    # Ordinary Dutch words that start with the same letters.
    _w("bur", "gerlijk"),
)
"""Names that may keep the word, blanked before the search."""

EXEMPT_FILES = (
    # The national-identity-number terms a custom form field may not use, in Dutch too (ADR 0055).
    "app/domain/form_fields.py",
    "tests/domain/test_form_fields.py",
)
"""Files that must name a word to refuse it; their words are written in pieces as well."""

SKIP_SUFFIXES = (".png", ".jpg", ".webp", ".woff", ".woff2", ".ico", ".gz", ".zip", ".pdf", ".svg")


def tracked_text_files() -> Iterator[Path]:
    """Yield every tracked text file except the lock file and the binaries."""
    git = shutil.which("git") or "git"
    listed = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    for raw in listed:
        name = raw.decode()
        if not name or name == "uv.lock" or name.endswith(SKIP_SUFFIXES):
            continue
        if name in EXEMPT_FILES or name == "tests/test_terminology.py":
            continue
        path = ROOT / name
        if path.is_file():
            yield path


def sector_words_in(line: str) -> bool:
    """Return whether ``line`` names a client or a sector outside the allowed names."""
    cleaned = line
    for name in ALLOWED:
        cleaned = re.sub(re.escape(name), " ", cleaned, flags=re.IGNORECASE)
    return SECTOR.search(cleaned) is not None


OFFICE_SUFFIXES = (".docx", ".xlsx", ".pptx")
"""Office files: zip archives of XML parts, read with the standard library."""

_PARAGRAPH_END = re.compile(r"</[a-z]:p>|<w:br\b[^>]*>|<w:cr\b[^>]*>")
_TAB = re.compile(r"<w:tab\b[^>]*>")
_TAG = re.compile(r"<[^>]*>")
_ATTRIBUTE_VALUE = re.compile(r'="([^"]*)"')


def office_lines(path: Path) -> list[str]:
    """Return the text of an Office file: a line per paragraph, then one per attribute value.

    Word splits a paragraph into runs, so a word may sit in two of them; the runs of a paragraph
    are joined before the search. Attribute values hold alt texts and link targets.
    """
    lines: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for part in sorted(archive.namelist()):
            if not part.endswith((".xml", ".rels")):
                continue
            xml = archive.read(part).decode("utf-8", errors="replace")
            text = _TAG.sub("", _TAB.sub(" ", _PARAGRAPH_END.sub("\n", xml)))
            lines += [html.unescape(line) for line in text.splitlines()]
            lines += [
                html.unescape(v) for tag in _TAG.findall(xml) for v in _ATTRIBUTE_VALUE.findall(tag)
            ]
    return lines


def text_lines(path: Path) -> list[str] | None:
    """Return the lines of a tracked file, or ``None`` for a binary the guard cannot read."""
    if path.suffix in OFFICE_SUFFIXES:
        return office_lines(path)
    try:
        return path.read_text().splitlines()
    except UnicodeDecodeError:
        return None


def violations() -> list[str]:
    """Return ``file:line: text`` for every tracked line that names a client or a sector."""
    found = []
    for path in tracked_text_files():
        lines = text_lines(path)
        if lines is None:
            continue
        found.extend(
            f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:100]}"
            for number, line in enumerate(lines, start=1)
            if sector_words_in(line)
        )
    return found


def test_no_client_and_no_sector_words_in_the_repository() -> None:
    assert violations() == [], "keep the repository neutral (ADR 0115)"


def test_the_guard_sees_the_words() -> None:
    assert sector_words_in("a " + _w("citi", "zen") + " books")
    assert sector_words_in("een " + _w("bur", "ger") + " boekt")
    assert sector_words_in(_w("Belas", "tingdienst"))
    assert sector_words_in("<" + _w("g", "ov-booking") + ">")
    # Slipped through in the demo seed's subjects and in examples (2026-11-08).
    assert sector_words_in(_w("Huur", "toe", "slag"))
    assert sector_words_in(_w("Belas", "tingen"))
    assert sector_words_in("vragen die " + _w("inwo", "ners") + " beantwoorden")
    assert sector_words_in(_w("Vergun", "ningen") + " Loket")
    # The default slug of the test groups, and so of many example addresses (2026-11-08).
    assert sector_words_in("/g/" + _w("aan", "gifte") + "-2026")
    assert sector_words_in("team-" + _w("Aan", "giftes") + "@example.org")


def test_the_guard_reads_the_text_of_an_office_file(tmp_path: Path) -> None:
    document = tmp_path / "plates.docx"
    word_in_two_runs = "<w:r><w:t>Bur</w:t></w:r><w:r><w:t>gers boeken</w:t></w:r>"
    body = (
        f"<w:p><w:r><w:t>De</w:t></w:r><w:tab/>{word_in_two_runs}</w:p>"
        "<w:p><w:r><w:t>Deelnemers</w:t></w:r></w:p>"
    )
    link = f'<Relationship Target="https://{_w("rij", "ks")}.example"/>'
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
        archive.writestr("word/_rels/document.xml.rels", f"<Relationships>{link}</Relationships>")
        archive.writestr("word/media/image1.png", b"\x89PNG")

    lines = office_lines(document)

    assert "De " + _w("Bur", "gers") + " boeken" in lines
    assert "Deelnemers" in lines
    assert sum(sector_words_in(line) for line in lines) == 2
    assert text_lines(document) == lines


def test_the_guard_lets_the_allowed_names_through() -> None:
    assert not sector_words_in(_w("PKI", "overheid", " certificates").replace("PKIover", "PKIover"))
    assert not sector_words_in("(" + _w("G", "OV.UK") + " and NL Design System)")
    assert not sector_words_in("een " + _w("bur", "gerlijk") + " woord")


def test_the_exempt_files_exist() -> None:
    assert all((ROOT / name).exists() for name in EXEMPT_FILES)


ANONYMISED = re.compile(r"anonim|anonymi|geanonim", re.IGNORECASE)
"""What a purge does is delete fields from the appointment; the booking is not made anonymous
(ADR 0102): the provider links and times can identify a person together with data held
elsewhere. The texts say "deleted" / "verwijderd"."""


def test_the_texts_and_docs_never_say_the_booking_is_anonymised() -> None:
    found = [
        f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:100]}"
        for path in tracked_text_files()
        if path.suffix in (".md", ".po", ".html", ".js", ".css")
        and not (
            path.parent.name == "adr" and int(path.name[:4])
            if path.name[:4].isdigit()
            else 999 <= 82
        )
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if ANONYMISED.search(line)
        and "never anonymised" not in line
        and 'never "anonymised"' not in line
        and not path.name.startswith(("0102-", "HANDOVER"))
        and path.name != "adr-status.md"
    ]
    assert found == [], "say deleted / verwijderd, never anonymised (ADR 0102)"
