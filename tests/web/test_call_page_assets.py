"""The call page's own scripts and styles (ADR 0123 steps 4b and 4c).

The scripts are checked for what they must never do (reach anything but the token and the
SDK file, set a style, build markup, log), for the texts they read (the server renders every one,
once), for their shape (each under 500 lines, each with a header that says what it owns and never
does) and the catalogue's texts of the page for the supplier's name (point 14). The behaviour
itself is in ``e2e/tests/call-page-device-check.spec.ts`` and ``call-page-joining.spec.ts``, the
way this repository tests its scripts.
"""

import re
from pathlib import Path

from app.i18n import CATALOGUE_DIR, load_catalogues

STATIC = Path("app/web/static")
SCRIPT_FILES = (
    "call-devices.js",
    "call-connection.js",
    "call-media.js",
    "call-controls.js",
    "call-page.js",
)
"""In the order the page loads them: the four that only define, then the page that starts them."""
SOURCES = {name: (STATIC / name).read_text() for name in SCRIPT_FILES}
SCRIPT = "\n".join(SOURCES.values())
STYLES = (STATIC / "call-page.css").read_text()
TEMPLATE = Path("app/api/templates/call.html").read_text()
SUPPLIER = ("webex", "cisco", "wbx2")
"""Words that name the supplier or its product; a participant text of the page has none."""
PAGE_KEYS = ("callpage.", "call.title", "call.unavailable")


def _code() -> str:
    """The scripts without their comments, strings left alone."""
    return "\n".join(
        line for line in SCRIPT.splitlines() if not line.lstrip().startswith(("//", "/*", "*"))
    )


def test_the_script_reaches_nothing_outside_the_browser_but_the_token_and_the_sdk() -> None:
    forbidden = re.compile(
        r"XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts"
        r"|\bimport\s*\(|\bimport\s+\w|localStorage|sessionStorage|indexedDB|document\.cookie"
        r"|https?://|wss?://|new Worker|serviceWorker|\bconsole\b"
    )

    assert forbidden.search(_code()) is None
    # One request, to the address the server gave; one other file, with its checksum.
    assert re.findall(r"\bfetch\s*\(([^,)]*)", _code()) == ["tokenUrl"]
    assert re.findall(r'createElement\("script"\)', _code()) == ['createElement("script")']
    assert re.findall(r"\.src\s*=\s*(\w+)", _code()) == ["sdkSource"]
    assert re.findall(r"\.integrity\s*=\s*(\w+)", _code()) == ["sdkIntegrity"]


def test_the_token_is_never_written_where_a_script_or_a_reader_could_find_it() -> None:
    # Where a value could end up in the page, an address or a storage: none of them takes it.
    assert not re.search(
        r"(textContent|dataset|setAttribute|href|\.value|\.src)[^;]*call\.(token|link)", _code()
    )


def test_each_script_has_a_header_and_stays_under_500_lines() -> None:
    for name, source in SOURCES.items():
        assert len(source.splitlines()) < 500, name
        header = source.split("*/", 1)[0]
        assert "Owns:" in header or "Never:" in header, name
        assert "Never:" in header, name


def test_the_page_loads_the_scripts_in_the_order_that_lets_the_last_start_the_others() -> None:
    order = re.findall(r"static_url\('(call-[a-z]+\.js)'\)", TEMPLATE)

    assert order == list(SCRIPT_FILES)


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
    names |= {  # "say_failed_" + reason of fail()
        f"say_failed_{reason}"
        for reason in (
            "attempts",
            "generic",
            "media",
            "lost",
            "not_open",
            "rate_limited",
            "unavailable",
        )
    }
    return {name for name in names if not name.endswith("_")}


def test_every_text_the_script_reads_is_rendered_by_the_server_and_no_other() -> None:
    rendered = set(re.findall(r'data-text="(\w+)"', TEMPLATE))
    # The two states that are over carry their sentence as `data-text="say_{{ name }}"`.
    rendered |= {"say_left", "say_ended"} if 'data-text="say_{{ name }}"' in TEMPLATE else set()

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


def test_neither_the_template_nor_the_styles_name_the_supplier() -> None:
    """The markup the server renders and the styles never name the supplier (ADR 0123 point 14).

    One thing is left alone on purpose: the address of the vendored file, ``/static/vendor/webex/``,
    which the server writes into an attribute of the page when the file is whole. It is an address,
    not a text for the participant, and point 14 says the addresses the page talks to stay visible
    in the browser's own tools; the template itself holds no such word.
    """
    for source in (TEMPLATE, STYLES):
        assert not any(name in source.lower() for name in SUPPLIER)


def test_the_script_names_the_supplier_only_as_the_one_global_the_sdk_file_defines() -> None:
    # Code, not a text: the SDK file puts one object on the window and the script must reach it.
    # The name occurs once, in call-connection.js, and nowhere else; no other supplier word does.
    counts = {
        (name, word): source.lower().count(word)
        for name, source in SOURCES.items()
        for word in SUPPLIER
    }

    assert {key: n for key, n in counts.items() if n} == {("call-connection.js", "webex"): 1}


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
