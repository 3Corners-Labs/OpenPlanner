"""Consistency of the shipped catalogues against each other and against the code."""

import re
from pathlib import Path

import pytest

from app.i18n import CATALOGUE_DIR, DEFAULT_LOCALE, load_catalogues, placeholders
from app.i18n.terms import AgentTermPlaceholders

APP = Path(__file__).resolve().parents[2] / "app"
REQUIRED = ("nl", "en")
_KEY = r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+"
# Keys passed to _() / gettext(locale, ...) in code and templates.
_CALLS = re.compile(rf"""(?:\b_\(\s*|\bgettext\([^,()]+,\s*)["']({_KEY})["']""")
_QUOTED = re.compile(rf"""["']({_KEY})["']""")


def _sources() -> str:
    files = [*APP.rglob("*.py"), *APP.rglob("*.html")]
    return "\n".join(path.read_text() for path in files)


@pytest.fixture(scope="module")
def catalogues() -> dict[str, dict[str, str]]:
    return load_catalogues(CATALOGUE_DIR)


def test_required_locales_ship(catalogues: dict[str, dict[str, str]]) -> None:
    assert set(REQUIRED) <= set(catalogues)
    assert DEFAULT_LOCALE in REQUIRED


def test_required_locales_have_the_same_keys(catalogues: dict[str, dict[str, str]]) -> None:
    reference = set(catalogues[DEFAULT_LOCALE])
    for locale in REQUIRED:
        assert set(catalogues[locale]) == reference, locale


def test_other_locales_only_contain_known_keys(catalogues: dict[str, dict[str, str]]) -> None:
    reference = set(catalogues[DEFAULT_LOCALE])
    for locale, messages in catalogues.items():
        assert set(messages) <= reference, locale


TERM = AgentTermPlaceholders.names
"""The agent term is available in every text, so two locales may use different forms of it."""


def test_placeholders_match_across_locales(catalogues: dict[str, dict[str, str]]) -> None:
    for key, source in catalogues[DEFAULT_LOCALE].items():
        for locale, messages in catalogues.items():
            if key in messages:
                assert placeholders(messages[key]) - TERM == placeholders(source) - TERM, (
                    locale,
                    key,
                )


def test_every_key_used_in_code_exists(catalogues: dict[str, dict[str, str]]) -> None:
    used = set(_CALLS.findall(_sources()))

    assert used, "no translation calls found; is the regex still right?"
    assert used <= set(catalogues[DEFAULT_LOCALE])


def test_every_catalogue_key_is_used(catalogues: dict[str, dict[str, str]]) -> None:
    quoted = set(_QUOTED.findall(_sources()))

    assert set(catalogues[DEFAULT_LOCALE]) <= quoted


DESIGN_RECORD = re.compile(r"\bADR[ -]?\d+\b")
"""A reference to a design record of docs/adr/: the team's shorthand, never the user's."""


def test_no_interface_text_names_a_design_record(catalogues: dict[str, dict[str, str]]) -> None:
    """The new-role page once said "(ADR 0049)" in its notice on entitlement claims."""
    for locale, messages in catalogues.items():
        named = sorted(key for key, text in messages.items() if DESIGN_RECORD.search(text))
        assert named == [], locale


SAME_SETTING = {
    "admin.group.slot_minutes": "admin.field.slot_minutes",
    "admin.group.notice": "admin.field.min_notice_minutes",
    "admin.group.horizon": "admin.field.horizon_days",
}
"""A fact of the group overview and the settings field it shows (overview fact: field)."""

UNIT = re.compile(r" \([^()]*\)$")
"""The unit a field's label ends with: " (minuten)"."""


@pytest.mark.parametrize("locale", REQUIRED)
@pytest.mark.parametrize(("fact", "field"), SAME_SETTING.items())
def test_the_group_overview_names_a_setting_as_its_field_does(
    catalogues: dict[str, dict[str, str]], locale: str, fact: str, field: str
) -> None:
    """The overview said "Minimale aanmeldtijd" where the settings said "aanlooptijd"."""
    messages = catalogues[locale]

    assert UNIT.sub("", messages[field]) == messages[fact]
