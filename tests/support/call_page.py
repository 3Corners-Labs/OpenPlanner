"""A small reader of the call page's markup for HTTP tests (ADR 0123 step 4b).

``Page`` parses the rendered HTML once and answers what the tests ask: the states of the page
(``[data-call-state]``), what is hidden, the buttons and headings, the text and the raw markup of
one part, what the page refers to and whether it holds inline code. It is a reader, not a
browser: ``hidden`` means the attribute, as the page's script sees it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import Any

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.adapters.integrations import Integrations
from app.config import Settings
from app.domain.ports import Clock
from app.domain.scheduling import JoinClient
from tests.conftest import app_client

VOID = frozenset({"meta", "link", "input", "br", "img", "source", "hr", "wbr"})
HEADINGS = frozenset({"h1", "h2", "h3"})


@dataclass
class Node:
    """One element: its tag, attributes, children and the text and raw markup inside it."""

    tag: str
    attrs: dict[str, str | None]
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0
    source: str = ""
    own_text: str = ""

    @property
    def raw(self) -> str:
        """The element's markup, start tag to end tag."""
        return self.source[self.start : self.end]

    @property
    def text(self) -> str:
        """The element's own text, when it has no children of interest (a button's label)."""
        return self.own_text

    @property
    def hidden(self) -> bool:
        """Whether the element or one of its parents has the ``hidden`` attribute."""
        node: Node | None = self
        while node is not None:
            if "hidden" in node.attrs:
                return True
            node = node.parent
        return False

    def walk(self) -> list["Node"]:
        """Return this element and every element inside it, in document order."""
        found = [self]
        for child in self.children:
            found.extend(child.walk())
        return found

    def buttons(self) -> list["Node"]:
        """Return the buttons inside this element."""
        return [n for n in self.walk() if n.tag == "button"]

    def text_content(self) -> str:
        """Return all text inside, whitespace folded."""
        return " ".join("".join(self.chunks).split())


class _Reader(HTMLParser):
    """Builds the ``Node`` tree and collects what the page refers to."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.lines = [0]
        for line in source.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.root = Node("#root", {}, source=source, end=len(source))
        self.stack: list[Node] = [self.root]
        self.references: list[str] = []
        self.script_sources: list[str] = []
        self.inline_code: list[str] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        node = Node(tag, values, self.stack[-1], source=self.source, start=self._offset())
        self.stack[-1].children.append(node)
        for name, value in attrs:
            if name.startswith("on") or name == "style":
                self.inline_code.append(f"{tag} {name}")
            if value and (name == "src" or (name in {"href", "action"} and tag in {"link", "a"})):
                if not value.startswith(("tel:", "mailto:", "#")):
                    self.references.append(value)
        if tag == "script":
            if values.get("src"):
                self.script_sources.append(values["src"] or "")
            else:
                self.inline_code.append("script")
        if tag in VOID:
            node.end = node.start + len(self.get_starttag_text() or "")
            return
        self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                node = self.stack[index]
                node.end = self._offset() + len(f"</{tag}>")
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        for node in self.stack:
            node.chunks.append(data)
        self.stack[-1].own_text += data


class Page:
    """The call page as the tests read it."""

    def __init__(self, raw: str) -> None:
        reader = _Reader(raw)
        reader.feed(raw)
        reader.close()
        self.raw = raw
        self._root = reader.root
        self.references = reader.references
        self.script_sources = reader.script_sources
        self.inline_code = reader.inline_code

    def _find(self, attribute: str) -> list[Node]:
        return [n for n in self._root.walk() if attribute in n.attrs]

    def state_names(self) -> list[str]:
        """Return the names of the page's states, in order."""
        return [str(n.attrs["data-call-state"]) for n in self._find("data-call-state")]

    def state(self, name: str) -> "StateView":
        """Return the state ``name`` (``start``, ``check``, ``unavailable``)."""
        for node in self._find("data-call-state"):
            if node.attrs["data-call-state"] == name:
                return StateView(node)
        raise KeyError(name)

    def element(self, attribute: str) -> "StateView":
        """Return the first element that has ``attribute``."""
        return StateView(self._find(attribute)[0])

    def visible_headings(self) -> list[str]:
        """Return the text of the level-one headings that are not hidden."""
        return [n.text_content() for n in self._root.walk() if n.tag == "h1" and not n.hidden]


class StateView:
    """One part of the page, for assertions."""

    def __init__(self, node: Node) -> None:
        self._node = node

    @property
    def raw(self) -> str:
        """The part's markup."""
        return self._node.raw

    @property
    def attrs(self) -> dict[str, str | None]:
        """The part's own attributes."""
        return self._node.attrs

    @property
    def hidden(self) -> bool:
        """Whether the part is hidden by its own or a parent's ``hidden`` attribute."""
        return self._node.hidden

    def text(self) -> str:
        """Return the part's text, whitespace folded."""
        return self._node.text_content()

    def buttons(self) -> list[Node]:
        """Return the part's buttons; a button's ``text`` is its folded label."""
        found = self._node.buttons()
        for button in found:
            button.own_text = button.text_content()
        return found


async def set_group(client: httpx.AsyncClient, slug: str, **changes: object) -> None:
    """Change fields of the group ``slug`` of the app behind ``client`` (a seeded group)."""
    app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        group = await tx.get_group(slug)
        assert group is not None
        await tx.save_group(replace(group, **changes))  # type: ignore[arg-type]


@asynccontextmanager
async def embedded_app_client(
    settings: Settings,
    clock: Clock | None = None,
    integrations: Integrations | None = None,
    *,
    slug: str = "demo",
    **changes: object,
) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app with the group ``slug`` set to use the call page (ADR 0123 step 6a).

    The call page's routes answer only for a service that chose it, so a test of the page itself
    starts from a group that did; ``changes`` set further fields of the group.
    """
    async with app_client(settings, clock, integrations) as client:
        await set_group(client, slug, join_client=JoinClient.EMBEDDED, **changes)
        yield client
