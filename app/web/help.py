"""The manual: Markdown in ``docs/manual/{locale}/``, read on GitHub and shown in the portal.

One file per admin screen (a *topic*, ADR 0033). The first ``#`` heading is the title. A
section that explains one input starts with a marker on the line before its heading; the
portal shows that section in the input's (i) disclosure and anchors it on the help page::

    <!-- field: slot_minutes -->
    ### Duur van een afspraak

A section ends at the next heading of its level or higher, or at the next marker. The
splitter reads lines, so a manual file has no headings inside code blocks. Raw HTML is off:
the marker is removed before rendering and anything else in angle brackets is escaped.
Links to another manual file (``group-agenda.md``) become portal links (``/admin/help/...``).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from markupsafe import Markup

from app.domain.authorization import Permission

MANUAL_DIR = Path(__file__).resolve().parents[2] / "docs" / "manual"
"""The manual in the repository; the image ships it at the same place (Dockerfile)."""

FALLBACK_LOCALE = "nl"
"""Shown when a topic is missing in the admin's language (ADR 0004: Dutch default)."""

HELP_PATH = "/admin/help"
"""Where the portal shows the manual."""


@dataclass(frozen=True)
class HelpTopic:
    """One documented admin screen.

    Attributes:
        slug: File name without ``.md`` and the last part of the help URL.
        paths: The route templates of the screen; their pages link to the topic.
        permission: The screen's permission; the topic list only shows topics the admin can
            open (the matrix decides, ADR 0014).
    """

    slug: str
    paths: tuple[str, ...]
    permission: Permission


TOPICS = (
    HelpTopic("group-settings", ("/admin/groups/{slug}/settings",), Permission.GROUP_EDIT),
    HelpTopic(
        "organisation-contacts",
        ("/admin/organisations/{org}/contacts",),
        Permission.ORGANISATION_CLOSURE,
    ),
)
"""Every topic of the manual; a test checks the files and the admin pages against it."""

_BY_SLUG = {t.slug: t for t in TOPICS}
_BY_PATH = {path: t for t in TOPICS for path in t.paths}


def topic(slug: str) -> HelpTopic | None:
    """Return the topic called ``slug``, if any."""
    return _BY_SLUG.get(slug)


def topic_for_path(route_path: str) -> HelpTopic | None:
    """Return the topic of a route template such as ``/admin/groups/{slug}/settings``."""
    return _BY_PATH.get(route_path)


class ManualError(ValueError):
    """A manual file breaks the conventions of this module (a bug in the repository)."""


@dataclass(frozen=True)
class HelpSection:
    """The explanation of one input.

    Attributes:
        key: The input's name, from the marker.
        heading: The section's heading as written.
        html: The body below the heading, rendered.
    """

    key: str
    heading: str
    html: Markup


@dataclass(frozen=True)
class HelpPage:
    """One topic in one language, ready for a template.

    Attributes:
        slug: The topic.
        locale: The language the text is in (may be the fallback).
        title: The first heading.
        html: The whole page below the title; marked headings carry ``id="field-<key>"``.
        sections: The marked sections by input name, in page order.
        topic_links: Topics this page links to, for the link check.
    """

    slug: str
    locale: str
    title: str
    html: Markup
    sections: Mapping[str, HelpSection] = field(default_factory=dict)
    topic_links: tuple[str, ...] = ()

    def more(self, key: str) -> Markup | None:
        """Return the explanation of input ``key`` for its (i) disclosure, if written."""
        section = self.sections.get(key)
        return section.html if section else None

    def link(self, key: str) -> str:
        """Return the help page URL anchored at input ``key``."""
        return f"{HELP_PATH}/{self.slug}#field-{key}"


_MARKER = re.compile(r"^<!--\s*field:\s*([a-z][a-z0-9_]*)\s*-->\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MANUAL_LINK = re.compile(r"^([a-z0-9][a-z0-9-]*)\.md(#[\w-]+)?$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _markdown() -> MarkdownIt:
    """Return the renderer: CommonMark plus tables, raw HTML off, manual links rewritten."""
    md = MarkdownIt("commonmark", {"html": False}).enable("table")

    def link_open(
        renderer: RendererHTML,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: EnvType,
    ) -> str:
        token = tokens[idx]
        match = _MANUAL_LINK.fullmatch(str(token.attrGet("href") or ""))
        if match:
            env.setdefault("topic_links", []).append(match.group(1))
            token.attrSet("href", f"{HELP_PATH}/{match.group(1)}{match.group(2) or ''}")
        return renderer.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", link_open)
    return md


_MD = _markdown()


def _trusted(html: str) -> Markup:
    """Mark renderer output as safe for templates.

    Only for output of ``_MD``: raw HTML is off, so every ``<`` in the source arrives
    escaped, and the only markup added here is a heading whose id matches ``_MARKER``.
    """
    return Markup(html)  # noqa: S704  # nosec B704 - see docstring; tested in test_help.py


@dataclass
class _Open:
    """A marked section being collected."""

    key: str
    level: int
    heading: str
    lines: list[str] = field(default_factory=list)


def parse(slug: str, locale: str, text: str) -> HelpPage:
    """Split and render one manual file.

    Raises:
        ManualError: No title, a marker not followed by a heading, or a field marked twice.
    """
    env: dict[str, list[str]] = {}
    title: str | None = None
    parts: list[str] = []
    sections: dict[str, HelpSection] = {}
    plain: list[str] = []
    pending: str | None = None
    current: _Open | None = None

    def flush_plain() -> None:
        if plain:
            parts.append(_MD.render("\n".join(plain), env))
            plain.clear()

    def close() -> None:
        nonlocal current
        if current is None:
            return
        body = _trusted(_MD.render("\n".join(current.lines), env))
        heading = _MD.renderInline(current.heading, env)
        parts.append(f'<h{current.level} id="field-{current.key}">{heading}</h{current.level}>')
        parts.append(body)
        sections[current.key] = HelpSection(current.key, current.heading, body)
        current = None

    for line in text.splitlines():
        marker = _MARKER.match(line)
        if marker:
            close()
            flush_plain()
            if marker.group(1) in sections or marker.group(1) == pending:
                raise ManualError(f"{slug}/{locale}: field {marker.group(1)} marked twice")
            pending = marker.group(1)
            continue
        heading = _HEADING.match(line)
        if pending is not None:
            if heading is None:
                if line.strip():
                    raise ManualError(f"{slug}/{locale}: marker {pending} needs a heading")
                continue
            current = _Open(pending, len(heading.group(1)), heading.group(2))
            pending = None
            continue
        if heading and title is None and len(heading.group(1)) == 1:
            title = heading.group(2)
            continue
        if heading and current is not None and len(heading.group(1)) <= current.level:
            close()
        (current.lines if current is not None else plain).append(line)
    if pending is not None:
        raise ManualError(f"{slug}/{locale}: marker {pending} needs a heading")
    close()
    flush_plain()
    if title is None:
        raise ManualError(f"{slug}/{locale}: no title")
    return HelpPage(
        slug=slug,
        locale=locale,
        title=title,
        html=_trusted("".join(parts)),
        sections=sections,
        topic_links=tuple(env.get("topic_links", ())),
    )


class Manual:
    """The manual files under ``root``, read once per topic and language.

    Args:
        root: The directory with one folder per locale.
    """

    def __init__(self, root: Path) -> None:
        """Remember where the manual is."""
        self._root = root
        self._read = cache(self._load)

    def page(self, slug: str, locale: str) -> HelpPage | None:
        """Return topic ``slug`` in ``locale``, else in Dutch, else ``None``.

        Only plain names are looked up, so a request can never name a path.
        """
        if not (_SLUG.fullmatch(slug) and _SLUG.fullmatch(locale)):
            return None
        return self._read(slug, locale) or self._read(slug, FALLBACK_LOCALE)

    def _load(self, slug: str, locale: str) -> HelpPage | None:
        """Read and parse one file, or return ``None`` if it does not exist."""
        path = self._root / locale / f"{slug}.md"
        if not path.is_file():
            return None
        return parse(slug, locale, path.read_text(encoding="utf-8"))


@cache
def shipped_manual() -> Manual:
    """Return the manual that ships with the application."""
    return Manual(MANUAL_DIR)
