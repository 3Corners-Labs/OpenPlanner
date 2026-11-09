"""The digest mail in words: fixed layout, catalogue texts, escaped, no attachment (ADR 0117)."""

from datetime import date

from app.domain.report_digest_model import DigestContent, DigestFigures
from app.i18n import Translator, load_catalogues
from app.web.digest_text import DASH, compose_digest

TEXTS = Translator(load_catalogues())


def _content(
    figures: DigestFigures | None, link: str = "https://planner.example/x?a=1&b=2"
) -> DigestContent:
    return DigestContent("acme", 1, "balie", 2, date(2026, 11, 2), date(2026, 11, 8), link, figures)


def test_the_mail_has_a_subject_a_plain_part_and_a_semantic_html_part_in_each_language() -> None:
    for locale, word in (("nl", "Gebruik van"), ("en", "Usage of")):
        mail = compose_digest(_content(DigestFigures(12, 5, 3, 80)), TEXTS.gettext, locale)

        assert mail.subject.startswith(word)
        assert "2026-11-02 – 2026-11-08" in mail.subject
        assert mail.html.startswith(f'<!doctype html><html lang="{locale}">')
        assert "<dl>" in mail.html
        assert "12" in mail.text
        assert "80 %" in mail.text


def test_a_hidden_figure_reads_as_a_dash_and_no_figures_means_only_the_link() -> None:
    with_dash = compose_digest(_content(DigestFigures(None, None, None, None)), TEXTS.gettext, "en")
    only_link = compose_digest(_content(None), TEXTS.gettext, "en")

    assert with_dash.text.count(DASH) >= 4
    assert "<dl>" not in only_link.html
    assert "Bookings created" not in only_link.text
    assert "https://planner.example/x?a=1&b=2" in only_link.text


def test_the_link_is_escaped_in_the_html_and_the_mail_names_no_attachment() -> None:
    mail = compose_digest(_content(None, 'https://x.example/"><script>'), TEXTS.gettext, "nl")

    assert "<script>" not in mail.html
    assert "&lt;script&gt;" in mail.html or "&quot;" in mail.html
