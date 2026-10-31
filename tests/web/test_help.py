"""The manual (ADR 0033): splitting into field sections, rendering, and the shipped files."""

import re
from pathlib import Path

import pytest

from app.web.help import MANUAL_DIR, TOPICS, Manual, ManualError, parse, topic, topic_for_path

PAGE = """# Groepsinstellingen

Hier stelt u de groep in.

## Planning

<!-- field: slot_minutes -->
### Duur van een afspraak

Hoe lang een afspraak duurt, in **minuten**.

Zie ook [de agenda](group-agenda.md) en [Webex](https://help.webex.com/).

<!-- field: horizon_days -->

### Vooruit boeken

Hoeveel dagen vooruit.

#### Voorbeeld

Dertig dagen.

## Koppelingen

Losse tekst <script>alert(1)</script>.
"""


def test_the_first_heading_is_the_title_and_not_part_of_the_body() -> None:
    page = parse("group-settings", "nl", PAGE)

    assert page.title == "Groepsinstellingen"
    assert "<h1" not in page.html
    assert "<p>Hier stelt u de groep in.</p>" in page.html


def test_marked_sections_get_an_anchor_and_their_own_body() -> None:
    page = parse("group-settings", "nl", PAGE)

    assert list(page.sections) == ["slot_minutes", "horizon_days"]
    assert '<h3 id="field-slot_minutes">Duur van een afspraak</h3>' in page.html
    slot = page.sections["slot_minutes"]
    assert slot.heading == "Duur van een afspraak"
    assert "<strong>minuten</strong>" in slot.html
    assert "Vooruit" not in slot.html


def test_a_section_keeps_its_subheadings_and_ends_at_the_next_heading_of_its_level() -> None:
    horizon = parse("group-settings", "nl", PAGE).sections["horizon_days"].html

    assert "<h4>Voorbeeld</h4>" in horizon
    assert "Dertig dagen." in horizon
    assert "Koppelingen" not in horizon


def test_links_to_other_manual_files_stay_inside_the_portal() -> None:
    page = parse("group-settings", "nl", PAGE)

    assert 'href="/admin/help/group-agenda"' in page.html
    assert 'href="https://help.webex.com/"' in page.html
    assert page.topic_links == ("group-agenda",)


def test_raw_html_in_the_manual_is_escaped() -> None:
    html = parse("group-settings", "nl", PAGE).html

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_more_returns_the_section_and_a_link_into_the_page() -> None:
    page = parse("group-settings", "nl", PAGE)

    assert page.more("slot_minutes") == page.sections["slot_minutes"].html
    assert page.link("slot_minutes") == "/admin/help/group-settings#field-slot_minutes"
    assert page.more("name") is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Geen titel\n", "title"),
        ("# T\n\n<!-- field: a -->\nGeen kop\n", "heading"),
        ("# T\n\n<!-- field: a -->\n## A\n\n<!-- field: a -->\n## B\n", "twice"),
        ("# T\n\n<!-- field: a -->\n", "heading"),
    ],
)
def test_a_malformed_page_is_refused(text: str, reason: str) -> None:
    with pytest.raises(ManualError, match=reason):
        parse("x", "nl", text)


def test_manual_falls_back_to_dutch_and_knows_missing_topics(tmp_path: Path) -> None:
    (tmp_path / "nl").mkdir()
    (tmp_path / "nl" / "group-settings.md").write_text(PAGE, encoding="utf-8")
    manual = Manual(tmp_path)

    assert manual.page("group-settings", "en").locale == "nl"
    assert manual.page("group-settings", "nl").title == "Groepsinstellingen"
    assert manual.page("nope", "nl") is None
    assert manual.page("../secrets", "nl") is None
    assert manual.page("group-settings\n", "nl") is None


def test_topics_are_found_by_route_path_and_slug() -> None:
    settings = topic_for_path("/admin/groups/{slug}/settings")

    assert settings is not None
    assert settings.slug == "group-settings"
    assert topic("group-settings") == settings
    assert topic_for_path("/admin/nope") is None
    assert topic("nope") is None


# --- the shipped manual -----------------------------------------------------------------------

REQUIRED_LOCALES = ("nl", "en")
SHIPPED = Manual(MANUAL_DIR)


@pytest.mark.parametrize("slug", [t.slug for t in TOPICS])
def test_every_topic_exists_in_dutch_and_english_with_the_same_fields(slug: str) -> None:
    pages = [SHIPPED.page(slug, code) for code in REQUIRED_LOCALES]

    assert [p.locale if p else None for p in pages] == list(REQUIRED_LOCALES)
    nl, en = (p for p in pages if p is not None)
    assert list(nl.sections) == list(en.sections)


@pytest.mark.parametrize("code", REQUIRED_LOCALES)
@pytest.mark.parametrize("slug", [t.slug for t in TOPICS])
def test_manual_links_point_to_existing_topics(slug: str, code: str) -> None:
    page = SHIPPED.page(slug, code)

    assert page is not None
    assert [link for link in page.topic_links if topic(link) is None] == []


@pytest.mark.parametrize("code", REQUIRED_LOCALES)
@pytest.mark.parametrize("slug", [t.slug for t in TOPICS])
def test_the_manual_needs_no_inline_styles(slug: str, code: str) -> None:
    """The CSP has no ``'unsafe-inline'`` for styles; aligned table columns would need it."""
    page = SHIPPED.page(slug, code)

    assert page is not None
    assert "style=" not in page.html


def test_every_manual_file_is_a_topic_and_listed_in_the_readme() -> None:
    readme = (MANUAL_DIR / "README.md").read_text(encoding="utf-8")
    slugs = {t.slug for t in TOPICS}

    for code in REQUIRED_LOCALES:
        files = {path.stem for path in (MANUAL_DIR / code).glob("*.md")}
        assert files == slugs
        for slug in slugs:
            assert f"({code}/{slug}.md)" in readme


def test_the_manual_never_links_participants_away() -> None:
    """Help is for the portal; participant templates do not link to the manual or GitHub."""
    participant_templates = Path(__file__).parents[2] / "app" / "api" / "templates"
    linked = [
        path.name
        for path in participant_templates.rglob("*.html")
        if re.search(r"github\.com|/admin/help|docs/manual", path.read_text(encoding="utf-8"))
    ]

    assert linked == []
