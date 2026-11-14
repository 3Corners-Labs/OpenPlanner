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


def test_the_token_goes_nowhere_but_the_sdk() -> None:
    # In memory, for the retries: read from the answer, handed to the SDK, forgotten. Never
    # written into the page, an address, a storage or a text.
    uses = [line.strip() for line in _code().splitlines() if re.search(r"\.(token|link)\b", line)]

    assert uses == [
        'call.token = "";',
        'call.link = "";',
        "if (call.token && call.expires - Date.now() > TOKEN_MARGIN_MS) return Promise.resolve();",
        'if (!body || !body.token || !body.link || isNaN(expires)) throw turnedAway("generic");',
        "call.token = body.token;",
        "call.link = body.link;",
        "credentials: { access_token: call.token },",
        "return within(client.meetings.create(call.link));",
    ]
    assert not re.search(
        r"(textContent|dataset|setAttribute|href|\.value)[^;]*call\.(token|link)", _code()
    )


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
    names |= {"say_left", "say_ended"}  # "say_" + kind of finish()
    names |= {  # "say_failed_" + reason of fail()
        f"say_failed_{reason}"
        for reason in ("attempts", "generic", "not_open", "rate_limited", "unavailable")
    }
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


def test_neither_the_template_nor_the_styles_name_the_supplier() -> None:
    for source in (TEMPLATE, STYLES):
        assert not any(name in source.lower() for name in SUPPLIER)


def test_the_script_names_the_supplier_only_as_the_one_global_the_sdk_file_defines() -> None:
    # Code, not a text: the SDK file puts one object on the window and the script must reach it.
    # No comment, string or identifier besides that one line says the supplier's name.
    found = [
        line.strip() for line in SCRIPT.splitlines() if any(n in line.lower() for n in SUPPLIER)
    ]

    assert found == ["return window.Webex;"]


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
