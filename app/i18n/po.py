"""Load the shipped gettext catalogues (``app/i18n/locales/<locale>.po``).

Catalogues are read from ``.po`` at startup, so no compile step or writable filesystem is
needed. Fuzzy and untranslated entries are skipped and fall back to the default locale.

Parsing takes about 20 ms, and every app start (each admin test starts one) used to repeat it.
The result is kept per directory and the files' modification times, so a changed file is read
again; every caller gets its own copy.
"""

from functools import cache
from pathlib import Path

from babel.messages.pofile import PoFileError, read_po

from app.i18n.negotiation import normalise_locale

CATALOGUE_DIR = Path(__file__).parent / "locales"
"""Directory with one ``<locale>.po`` file per shipped locale."""


def load_catalogues(directory: Path = CATALOGUE_DIR) -> dict[str, dict[str, str]]:
    """Return ``{locale: {key: text}}`` for every ``.po`` file in ``directory``.

    Raises:
        ValueError: If a file name is not a locale code, the file is malformed, or it
            contains plural entries (not supported yet).
    """
    files = tuple((path.name, path.stat().st_mtime_ns) for path in sorted(directory.glob("*.po")))
    return {locale: dict(messages) for locale, messages in _parse(directory, files).items()}


@cache
def _parse(directory: Path, files: tuple[tuple[str, int], ...]) -> dict[str, dict[str, str]]:
    """Parse the ``.po`` files of ``directory``; ``files`` (names, mtimes) keys the cache."""
    catalogues: dict[str, dict[str, str]] = {}
    for path in (directory / name for name, _ in files):
        locale = normalise_locale(path.stem)
        if locale is None or locale != path.stem:
            raise ValueError(f"{path.name}: file name must be a lower-case locale code")
        try:
            with path.open("rb") as handle:
                catalogue = read_po(handle, abort_invalid=True)
        except PoFileError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        messages: dict[str, str] = {}
        for message in catalogue:
            if not message.id:
                continue  # header entry
            if not isinstance(message.id, str):
                raise ValueError(f"{path.name}: plural entries are not supported yet")
            if message.fuzzy or not message.string:
                continue
            messages[message.id] = str(message.string)
        catalogues[locale] = messages
    return catalogues
