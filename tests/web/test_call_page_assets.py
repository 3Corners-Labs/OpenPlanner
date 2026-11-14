"""The call page's own script and styles, before the click that joins (ADR 0123 step 4b).

The script is checked for what it must never do (reach anything outside the browser, set a
style, build markup), for the texts it reads (the server renders every one), and the catalogue's
texts of the page for the supplier's name (point 14). The behaviour itself is in
``e2e/tests/call-page-device-check.spec.ts``, the way this repository tests its scripts.
"""

import re
from pathlib import Path

from app.i18n import CATALOGUE_DIR, load_catalogues

STATIC = Path("app/web/static")
SCRIPT = (STATIC / "call-page.js").read_text()
STYLES = (STATIC / "call-page.css").read_text()
TEMPLATE = Path("app/api/templates/call.html").read_text()
SUPPLIER = ("webex", "cisco", "wbx2")
"""Words that name the supplier or its product; a participant text of the page has none."""
PAGE_KEYS = ("callpage.", "call.title", "call.unavailable")


def _code() -> str:
    """The script without its comments, strings left alone."""
    return "\n".join(
        line for line in SCRIPT.splitlines() if not line.lstrip().startswith(("//", "/*", "*"))
    )


def test_the_script_reaches_nothing_outside_the_browser() -> None:
    forbidden = re.compile(
        r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts"
        r"|\bimport\s*\(|\bimport\s+\w|localStorage|sessionStorage|indexedDB|document\.cookie"
        r"|https?://|wss?://|new Worker|serviceWorker"
    )

    assert forbidden.search(_code()) is None


def test_the_script_sets_no_style_and_no_class_that_the_policy_would_refuse() -> None:
    assert ".style" not in _code()
    assert 'setAttribute("style"' not in _code()
    assert "cssText" not in _code()


def _read_by_the_script() -> set[str]:
    names = set(re.findall(r"texts\.(\w+)", SCRIPT))
    names |= set(re.findall(r'"((?:say|level)_\w+)"', SCRIPT))
    names |= {"on", "off"}  # texts[on ? "on" : "off"]
    names |= {f"say_{kind}_{state}" for kind in ("camera", "microphone") for state in ("on", "off")}
    names |= {f"say_without_{kind}" for kind in ("camera", "microphone", "both")}
    return {name for name in names if not name.endswith("_")}


def test_every_text_the_script_reads_is_rendered_by_the_server_and_no_other() -> None:
    rendered = set(re.findall(r'data-text="(\w+)"', TEMPLATE))

    assert _read_by_the_script() - rendered == set()
    assert rendered - _read_by_the_script() == set()


def test_the_script_has_no_text_of_its_own() -> None:
    # Every sentence of the page is in the catalogue; a quoted string of the script is a name
    # (an attribute, a key, a tag) and never a sentence of words.
    sentences = [
        s
        for s in re.findall(r'"([^"]*)"', _code())
        if re.search(r"[a-z]{3,} [a-z]{3,}", s) and s != "use strict"
    ]

    assert sentences == []


def test_the_styles_use_the_organisations_tokens_and_no_force() -> None:
    assert "!important" not in STYLES
    assert "style=" not in STYLES
    colours = [
        line
        for line in STYLES.splitlines()
        if re.search(r"#[0-9a-fA-F]{3,8}\b", line) and "--call-" not in line
    ]
    assert colours == []
    for media in ("forced-colors", "prefers-contrast"):
        assert media in STYLES


def test_neither_the_template_nor_the_script_nor_the_styles_name_the_supplier() -> None:
    for source in (TEMPLATE, SCRIPT, STYLES):
        assert not any(name in source.lower() for name in SUPPLIER)


def test_no_text_of_the_page_names_the_supplier_in_any_locale() -> None:
    catalogues = load_catalogues(CATALOGUE_DIR)
    found = {
        (locale, key)
        for locale, messages in catalogues.items()
        for key, text in messages.items()
        if key.startswith(PAGE_KEYS) and any(name in text.lower() for name in SUPPLIER)
    }

    assert found == set()
    assert any(key.startswith("callpage.") for key in catalogues["nl"])


def test_the_page_texts_are_formal_dutch() -> None:
    nl = load_catalogues(CATALOGUE_DIR)["nl"]
    informal = re.compile(r"\b(je|jij|jouw|jou)\b", re.IGNORECASE)

    assert [
        k for k, text in nl.items() if k.startswith("callpage.") and informal.search(text)
    ] == []
