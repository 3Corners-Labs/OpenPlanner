"""The repository is neutral: no client names and no sector words (ADR 0077, ADR 0115).

OpenPlanner is a generic booking platform. Its code, tests, docs, ADRs, catalogue texts and
commit messages name no client and no sector. The person who books and joins is the participant
(deelnemer). The words below are written in pieces so that this file does not contain them.

Names of external standards and services that are cited exactly stay in ``ALLOWED``, each with
its reason. A new entry needs a reason, the fix is otherwise the word.
"""

import re
import shutil
import subprocess
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
            _w("belasting", "dienst"),
            _w("fin", "[-_ ]?bld"),
            _w("rij", "ks"),
            _w("inkomsten", "belasting"),
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


def violations() -> list[str]:
    """Return ``file:line: text`` for every tracked line that names a client or a sector."""
    found = []
    for path in tracked_text_files():
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
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
    assert sector_words_in(_w("Belasting", "dienst"))
    assert sector_words_in("<" + _w("g", "ov-booking") + ">")


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
