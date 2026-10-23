from pathlib import Path

import pytest

from app.i18n.po import load_catalogues

PO = """\
msgid ""
msgstr ""
"Language: {locale}\\n"
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "home.title"
msgstr "{title}"

#, fuzzy
msgid "draft.key"
msgstr "Not reviewed yet"

msgid "untranslated.key"
msgstr ""

msgid "home.intro"
msgstr "Intro"
"""


def _write(directory: Path, locale: str, title: str) -> None:
    (directory / f"{locale}.po").write_text(PO.format(locale=locale, title=title))


def test_loads_one_catalogue_per_po_file(tmp_path: Path) -> None:
    _write(tmp_path, "nl", "Afspraak maken")
    _write(tmp_path, "en", "Make an appointment")

    catalogues = load_catalogues(tmp_path)

    assert catalogues == {
        "nl": {"home.title": "Afspraak maken", "home.intro": "Intro"},
        "en": {"home.title": "Make an appointment", "home.intro": "Intro"},
    }


def test_skips_fuzzy_and_untranslated_entries(tmp_path: Path) -> None:
    _write(tmp_path, "nl", "Titel")

    assert set(load_catalogues(tmp_path)["nl"]) == {"home.title", "home.intro"}


@pytest.mark.parametrize("name", ["not a locale", "NL"])
def test_rejects_file_names_that_are_not_lower_case_locales(tmp_path: Path, name: str) -> None:
    _write(tmp_path, name, "x")

    with pytest.raises(ValueError, match="locale"):
        load_catalogues(tmp_path)


def test_empty_directory_gives_no_catalogues(tmp_path: Path) -> None:
    assert load_catalogues(tmp_path) == {}


def test_rejects_plural_entries(tmp_path: Path) -> None:
    (tmp_path / "nl.po").write_text(
        'msgid "slot"\nmsgid_plural "slots"\nmsgstr[0] "slot"\nmsgstr[1] "slots"\n'
    )

    with pytest.raises(ValueError, match="plural"):
        load_catalogues(tmp_path)


def test_rejects_malformed_files(tmp_path: Path) -> None:
    (tmp_path / "nl.po").write_text('garbage line\nmsgid "home.title"\nmsgstr "x"\n')

    with pytest.raises(ValueError, match=r"nl\.po"):
        load_catalogues(tmp_path)
