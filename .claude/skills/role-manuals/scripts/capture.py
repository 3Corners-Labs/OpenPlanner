#!/usr/bin/env python3
"""Capture the screenshots of ``opnamelijst.md`` from a running local installation.

The screenshots are evidence that the software exists: every image comes from a real user
path on the real software (the participant page, the link in the email the worker sent, the
admin screens), never from the database or a test address. The only edits are cropping and a
solid light-grey bar, drawn with Pillow from the bounding box the page reports, over:

1. every remaining occurrence of the product name (any case, also inside addresses and email
   addresses) and its mark, after the footer was switched off through its real setting;
2. every occurrence of the contact-center words Contactcenter, Contactcentertaak, Webex
   Contact Center, WxCC and Agent Desktop (also in compounds), and a whole row, column or
   block that only holds such content; at an edge of the capture it is cropped off instead
   (rule added by the coordinator: the product owner does not want that integration named).

Every bar and crop is written to ``bewijs/bewijslog.md``. Besides the three preparation steps of
the list, the story switches off the demo seed's question "Waar gaat uw vraag over?" through
its real "Uitzetten" button, because its options are sector words (redactiestatuut §2.6).

Usage (from the checkout of the software, or anywhere with ``OPENPLANNER_CHECKOUT`` set to it)::

    python3 scripts/capture.py                 # the whole story on a fresh stack
    python3 scripts/capture.py --list          # the steps in story order
    python3 scripts/capture.py --only bh-12-medewerkers,bh-13-medewerker
    python3 scripts/capture.py --from pl-07-agenda-dag
    python3 scripts/capture.py --with-webex    # also try bb-20, bb-21, mw-20 (need Webex)

A whole run makes bookings, confirms them through the emailed links, moves and cancels them,
and changes settings through the portal, in the order of the story in ``opnamelijst.md``. It
therefore needs a fresh stack (``stack.sh down`` and ``up`` of the verify-openplanner skill):
the booking rate limit (10 per hour per address) and the limit of three bookings per email
address leave no room for a second story on the same stack. Steps that only read a screen can
be repeated with ``--only``; the state of the story (booking references, Mailpit message ids,
the times of today's staffing) is kept in ``bewijs/opname.json``, never a booking-link token
or a password.

The installation must answer ``/api/v1/instance`` with the installation id that the
verify-openplanner run recorded; otherwise the script stops before it drives anything.
Development users are those of ``dev/keycloak/README.md``; their password is read from
``dev/keycloak/openplanner-realm.json`` at run time and never printed, logged or stored.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

# --------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
_OUT = Path(os.environ["CAPTURE_OUT"]) if os.environ.get("CAPTURE_OUT") else ROOT
"""``CAPTURE_OUT`` sends images, state and log elsewhere (a trial run); the default is the
project."""
IMG = _OUT / "img"
BEWIJS = _OUT / "bewijs"
STATE_FILE = BEWIJS / "opname.json"
LOG_FILE = BEWIJS / "bewijslog.md"
LIST_FILE = ROOT / "opnamelijst.md"

CHECKOUT = Path(os.environ.get("OPENPLANNER_CHECKOUT") or Path.cwd())
"""Checkout of the running stack: ``OPENPLANNER_CHECKOUT``, else the working directory."""
REALM_FILE = CHECKOUT / "dev" / "keycloak" / "openplanner-realm.json"

TZ = ZoneInfo("Europe/Amsterdam")
VIEWPORT = {"width": 1280, "height": 800}
WIDE = {"width": 1440, "height": 900}
"""Window of the overviews in ``WIDE_IDS``: at 1280 px their tables lose the right-hand column."""
WIDE_IDS = {"pl-07-agenda-dag", "pl-08-agenda-week", "bh-14-rechtenmatrix", "bh-17-auditlog"}
MOBILE = {"width": 390, "height": 844}
SCALE = 2
EMAIL_WIDTH = 700
GREY = (208, 208, 208)
"""The solid light grey of every redaction bar."""
PAD = 2
"""CSS pixels around a redaction box, so anti-aliased edges of the glyphs are covered too."""

GROUP = "demo"
ORG = "demo"
GROUP_NAME = "Demo: videogesprek"
PEOPLE = {
    "sanne": ("Sanne", "Jansen", "sanne.jansen@voorbeeld.nl"),
    "ahmed": ("Ahmed", "el Amrani", "ahmed.elamrani@voorbeeld.nl"),
    "pieter": ("Pieter", "de Bakker", "pieter@dekorenaar.voorbeeld.nl"),
}
"""The made-up participants of the list (Pieter de Bakker of Bakkerij De Korenaar)."""

ROLES = {
    "super": "applicatiebeheerder (`super`)",
    "orgadmin": "organisatiebeheerder (`orgadmin`)",
    "groupadmin": "planner (`groupadmin`)",
    "agent": "medewerker (`agent`)",
    "auditor": "compliance en privacy (`auditor`)",
}

SUBJECT = {
    "confirm_request": re.compile(r"^Bevestig uw afspraak met "),
    "confirmation": re.compile(r"^Bevestiging van uw videogesprek"),
    "change": re.compile(r"^Uw videogesprek is verplaatst"),
    "cancellation": re.compile(r"is geannuleerd$"),
}
"""Subjects of the participant's emails (message.<kind>.subject in nl.po)."""

NO_VIDEO_PHRASES = (
    "Geen meeting",
    "Meeting klaar",
    "Videolink mislukt",
    "Videolink wordt klaargezet",
    "Het videogesprek wordt nog klaargezet",
    "Het videogesprek kon niet worden klaargezet",
    "Niet opgepakt",
    "Deelnemer nog niet in het gesprek",
)
"""Texts that show because this installation has no Webex Meetings (logged per capture)."""

WEBEX_IDS = ("bb-20-deelnemen", "bb-21-proefgesprek", "mw-20-gesprek")

TOKEN_IN_PATH = re.compile(r"/b/[A-Za-z0-9_-]{20,}")


def now_local() -> dt.datetime:
    """Return the current time in the group's time zone."""
    return dt.datetime.now(TZ)


def stamp() -> str:
    """Return the current local time as written in the log."""
    return now_local().strftime("%Y-%m-%d %H:%M:%S %Z")


# --------------------------------------------------------------------------------------------
# The installation: guard, run record, credentials, mail
# --------------------------------------------------------------------------------------------


@dataclass
class Stack:
    """The verify-openplanner run whose installation this script drives."""

    web: str
    keycloak: str
    mail: str
    installation: str
    run_id: str
    commit: str
    image: str
    local_changes: str


def load_stack() -> Stack:
    """Read the ports, commit and installation id that stack.sh up recorded."""
    root = CHECKOUT / ".verify-openplanner"
    current = (root / "current").read_text().strip() if (root / "current").exists() else ""
    if not re.fullmatch(r"\d{8}-\d{6}", current):
        sys.exit("capture.py: no verify-openplanner run is up (no .verify-openplanner/current)")
    env: dict[str, str] = {}
    for line in (root / current / "stack.env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key:
            env[key] = value
    if env.get("VERIFY_STATE") != "up":
        sys.exit(f"capture.py: run {current} is {env.get('VERIFY_STATE')}, not up")
    return Stack(
        web=f"http://127.0.0.1:{env['VERIFY_WEB_PORT']}",
        keycloak=f"http://127.0.0.1:{env['VERIFY_KC_PORT']}",
        mail=f"http://127.0.0.1:{env['VERIFY_MAIL_PORT']}",
        installation=env.get("INSTANCE_ID", ""),
        run_id=current,
        commit=env.get("BUILT_FROM_COMMIT", "?"),
        image=env.get("IMAGE_ID", "?"),
        local_changes=env.get("BUILT_WITH_LOCAL_CHANGES", "?"),
    )


def http_json(url: str) -> Any:
    """GET ``url`` and return its JSON body."""
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - local stack only
        return json.loads(response.read().decode())


def assert_installation(stack: Stack, expected: str) -> None:
    """Stop unless the web port answers the installation id the run recorded."""
    try:
        answer = http_json(f"{stack.web}/api/v1/instance")
    except OSError as error:
        sys.exit(f"capture.py: {stack.web}/api/v1/instance does not answer ({error})")
    found = answer.get("instance_id")
    if found != expected or found != stack.installation:
        sys.exit(
            f"capture.py: {stack.web} answers installation {found}, expected {expected} "
            f"(run {stack.run_id} recorded {stack.installation}); stopping"
        )


def realm_password(user: str) -> str:
    """Return a development user's password from the realm file; never print it."""
    realm = json.loads(REALM_FILE.read_text())
    for entry in realm.get("users", []):
        if entry.get("username") == user:
            for credential in entry.get("credentials", []):
                if credential.get("type") == "password":
                    return str(credential["value"])
    raise RuntimeError(f"the development realm has no user {user} with a password")


class Mail:
    """The participant's mailbox in Mailpit (search by recipient, read one message)."""

    def __init__(self, base: str, web: str) -> None:
        """Use the Mailpit API at ``base``; booking links start with the web address ``web``."""
        self.base = base
        self.link = re.compile(re.escape(web) + r"/b/[A-Za-z0-9_-]{20,}(?:/[a-z]+)?")

    def ids(self, email: str) -> set[str]:
        """Return the ids of every message to ``email``."""
        return {m["ID"] for m in self.search(email)}

    def search(self, email: str) -> list[dict[str, Any]]:
        """Return the messages to ``email``, newest first."""
        query = urllib.parse.quote(f'to:"{email}"')
        return http_json(f"{self.base}/api/v1/search?query={query}&limit=100").get("messages") or []

    def message(self, message_id: str) -> dict[str, Any]:
        """Return one message whole (subject, text, HTML)."""
        return http_json(f"{self.base}/api/v1/message/{message_id}")

    def wait(
        self, email: str, subject: re.Pattern[str], seen: set[str], timeout: int = 150
    ) -> dict[str, Any]:
        """Wait for a new message to ``email`` whose subject matches; return it whole."""
        end = time.time() + timeout
        while time.time() < end:
            for summary in self.search(email):
                if summary["ID"] not in seen and subject.search(summary["Subject"]):
                    seen.add(summary["ID"])
                    return self.message(summary["ID"])
            time.sleep(2)
        raise RuntimeError(f"no email matching {subject.pattern!r} arrived within {timeout} s")

    def links(self, message: dict[str, Any]) -> list[str]:
        """Return the booking links of a message's plain-text part (kept in memory only)."""
        return list(dict.fromkeys(self.link.findall(message.get("Text") or "")))


# --------------------------------------------------------------------------------------------
# Redaction: find the product name and its mark on the page, cover them on the PNG
# --------------------------------------------------------------------------------------------

DETECT_JS = (
    r"""
() => {
  const NAME = /openplanner/gi;
  const TERMS = /Webex Contact Center|Contact Center|Agent Desktop|"""
    r"""[\p{L}\p{N}_-]*(?:contactcenter|contact-center|wxcc)[\p{L}\p{N}_-]*/giu;
  const VERSION = /Geen releaseversie \(ontwikkelversie\)|\bGeen release\b|"""
    r"""\b[Vv]ersie onbekend\b|sha256:[0-9a-f]{12,64}|"""
    r"""(?<![\w.])v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?(?![\w.])|(?<![\w.])v\d+\.\d+(?![\w.])|"""
    r"""(?<![0-9A-Za-z])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}(?![0-9A-Za-z])|"""
    r"""(?<![\w-])dev(?![\w-])/g;
  const found = [];
  const shown = (el) => {
    if (!el) return false;
    if (typeof el.checkVisibility === 'function' &&
        !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const snippet = (t) => (t || '').replace(/\s+/g, ' ').trim().slice(0, 140);
  const push = (kind, label, context, r) => {
    if (!r || r.width < 1 || r.height < 1) return;
    found.push({kind, label, context, x: r.left, y: r.top, w: r.width, h: r.height});
  };
  const canvas = document.createElement('canvas');
  const measure = (el, text) => {
    const ctx = canvas.getContext('2d');
    const cs = getComputedStyle(el);
    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    return ctx.measureText(text).width;
  };
  const inControl = (el, text, kind, re) => {
    re.lastIndex = 0;
    let m;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const left = r.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
    const right = r.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
    while ((m = re.exec(text))) {
      if (!m[0]) { re.lastIndex++; continue; }
      const x = left + measure(el, text.slice(0, m.index));
      const atEnd = m.index + m[0].length >= text.trimEnd().length;
      const w = atEnd ? right - x : Math.min(measure(el, m[0]) * 1.08 + 4, right - x);
      if (w > 1) push(kind, m[0], snippet(text), {left: x, top: r.top, width: w, height: r.height});
    }
  };
  const inText = (node, re, kind) => {
    const text = node.nodeValue;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text))) {
      if (!m[0]) { re.lastIndex++; continue; }
      const range = document.createRange();
      range.setStart(node, m.index);
      range.setEnd(node, m.index + m[0].length);
      for (const r of range.getClientRects()) """
    r"""push(kind, m[0], snippet(node.parentElement.innerText || text), r);
    }
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      const p = n.parentElement;
      if (!p || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'TITLE', 'TEXTAREA']"""
    r""".includes(p.tagName)) {
        return NodeFilter.FILTER_REJECT;
      }
      const t = n.nodeValue || '';
      VERSION.lastIndex = 0;
      return (/openplanner|contact ?-?center|wxcc|agent desktop/i.test(t) || VERSION.test(t)) """
    r"""? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });
  let node;
  while ((node = walker.nextNode())) {
    if (!shown(node.parentElement)) continue;
    inText(node, NAME, 'productnaam');
    inText(node, TERMS, 'contactcenterterm');
    inText(node, VERSION, 'versie');
  }
  for (const mark of document.querySelectorAll('svg.brand-mark')) {
    if (shown(mark)) push('beeldmerk', 'beeldmerk van het product', """
    r"""snippet(mark.parentElement && mark.parentElement.parentElement """
    r"""? mark.parentElement.parentElement.innerText : ''), mark.getBoundingClientRect());
  }
  for (const img of document.querySelectorAll('img')) {
    const src = (img.currentSrc || img.src || '') + ' ' + (img.alt || '');
    if (/openplanner|brand-mark|product-mark/i.test(src) && shown(img)) """
    r"""push('beeldmerk', 'afbeelding van het beeldmerk', snippet(img.alt), """
    r"""img.getBoundingClientRect());
  }
  for (const el of document.querySelectorAll("""
    r"""'input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea')) {
    if (!shown(el)) continue;
    const value = el.value || el.getAttribute('placeholder') || '';
    for (const [re, kind] of [[NAME, 'productnaam in een veld'], """
    r"""[TERMS, 'contactcenterterm in een veld'], [VERSION, 'versie in een veld']]) {
      re.lastIndex = 0;
      if (!re.test(value)) continue;
      if (el.tagName === 'TEXTAREA') """
    r"""push(kind, 'tekstvak', snippet(value), el.getBoundingClientRect());
      else inControl(el, value, kind, re);
    }
  }
  for (const el of document.querySelectorAll('select')) {
    if (!shown(el)) continue;
    for (const [re, kind] of [[NAME, 'productnaam in een keuzelijst'], """
    r"""[TERMS, 'contactcenterterm in een keuzelijst'], [VERSION, 'versie in een keuzelijst']]) {
      if (el.multiple || el.size > 1) {
        for (const o of el.options) { re.lastIndex = 0; if (re.test(o.text)) """
    r"""push(kind, o.text, snippet(o.text), o.getBoundingClientRect()); }
      } else if (el.selectedIndex >= 0) {
        const text = el.options[el.selectedIndex].text;
        re.lastIndex = 0;
        if (re.test(text)) inControl(el, text, kind, re);
      }
    }
  }
  return found;
}
"""
)

COLUMN_JS = (
    r"""
(table, header) => {
  const heads = [...table.querySelectorAll('thead th')];
  const index = heads.findIndex((th) => th.innerText.trim().toLowerCase() === header.toLowerCase());
  if (index < 0) return [];
  const cells = [heads[index]];
  for (const row of table.querySelectorAll('tbody tr')) {
    const own = [...row.children];
    if (own.length === heads.length) cells.push(own[index]);
  }
  return cells.map((c) => { const r = c.getBoundingClientRect();
    return {x: r.left, y: r.top, w: r.width, h: r.height, """
    r"""context: c.innerText.replace(/\s+/g, ' ').trim().slice(0, 60)}; });
}
"""
)

PAGE_FACTS_JS = (
    r"""
(phrases) => {
  const text = [...document.querySelectorAll("""
    r"""'.chip, [role=status], [role=alert], .alert, p, dd, td, li')]
    .filter((n) => n.offsetParent !== null).map((n) => n.innerText).join('\n');
  const h1 = [...document.querySelectorAll('h1')].map((h) => h.innerText.trim()).filter(Boolean);
  const notices = [...document.querySelectorAll('[role=status], [role=alert], .alert, .notice')]
    .filter((n) => n.offsetParent !== null)
    .map((n) => n.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const present = phrases.filter((p) => text.includes(p));
  return {h1, notices: [...new Set(notices)].slice(0, 6), present};
}
"""
)


@dataclass
class Shot:
    """How one capture is cut from the page."""

    mode: str = "viewport"
    """``viewport`` (the window as it is), ``top`` (from the top down to ``until``),
    ``full`` (the whole page), ``region`` (``target`` with a margin) or ``email``."""
    until: Locator | None = None
    target: Locator | None = None
    margin: int = 24
    full_width: bool = True
    crop_env_bar: bool = True
    bottom: Locator | None = None
    """``region``: end the cut here instead of at the bottom of ``target`` (crops off what
    follows)."""
    cover: list[tuple[Locator, str]] = field(default_factory=list)
    """Whole blocks to cover (each with what it is), when they only hold contact-center content."""
    cover_versions: list[tuple[Locator, str]] = field(default_factory=list)
    """Whole values of version fields to cover (each with what it is), rule 3."""
    cover_columns: list[tuple[Locator, str, str]] = field(default_factory=list)
    """Table columns to cover cell by cell: (table, header text, what it is)."""


def _union(boxes: list[dict[str, float]]) -> dict[str, float]:
    """Return the smallest box that holds every box of ``boxes``."""
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = max(b["y"] + b["height"] for b in boxes)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _settle(page: Page) -> None:
    """Wait until the page has loaded, its fonts are ready and late layout has had a moment."""
    page.wait_for_load_state("load")
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_timeout(400)


def _stretch(page: Page) -> tuple[dict[str, int], int]:
    """Make the window as tall as the page so a capture needs no scrolling; return the old size."""
    old = page.viewport_size or VIEWPORT
    page.evaluate("window.scrollTo(0, 0)")
    height = old["height"]
    for _ in range(4):
        doc = int(page.evaluate("Math.ceil(document.documentElement.scrollHeight)"))
        if doc <= height:
            break
        height = min(doc, 16000)
        page.set_viewport_size({"width": old["width"], "height": height})
        page.wait_for_timeout(300)
    return old, height


# --------------------------------------------------------------------------------------------
# The run: browser contexts per role, the story state, the records of the log
# --------------------------------------------------------------------------------------------


class NotYet(Exception):  # noqa: N818 - a signal, not an error; the other script may catch it
    """A step that must wait for a moment of the day (the queue window)."""

    def __init__(self, until: dt.datetime, why: str) -> None:
        """Wait until ``until``, for the reason ``why``."""
        super().__init__(why)
        self.until = until
        self.why = why


class Skip(Exception):  # noqa: N818 - a signal, not an error; the other script may catch it
    """A step that cannot be captured on this installation; the reason goes into the log."""


@dataclass
class Run:
    """Everything one invocation shares: browser, sessions, state and the log records."""

    stack: Stack
    mail: Mail
    browser: Any
    state: dict[str, Any]
    contexts: dict[str, BrowserContext] = field(default_factory=dict)
    pages: dict[str, Page] = field(default_factory=dict)
    staff_viewport: dict[str, int] = field(default_factory=lambda: dict(VIEWPORT))
    """The window of the staff pages: ``VIEWPORT``, or ``WIDE`` while a step of ``WIDE_IDS``
    runs."""

    # ---- state ---------------------------------------------------------------------------

    def save(self) -> None:
        """Write the story state and the records (no token, no password)."""
        BEWIJS.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.state, indent=2, ensure_ascii=False) + "\n")

    @property
    def records(self) -> dict[str, Any]:
        """Return the capture records by id (kept in the story state)."""
        return self.state.setdefault("records", {})

    @property
    def bookings(self) -> dict[str, Any]:
        """Return the bookings of the story by key (kept in the story state)."""
        return self.state.setdefault("bookings", {})

    def booking(self, key: str) -> dict[str, Any]:
        """Return a booking of the story or explain which step makes it."""
        if key not in self.bookings:
            raise Skip(
                f"de boeking {key} bestaat nog niet (eerdere stap van het verhaal niet gedraaid)"
            )
        return self.bookings[key]

    def note_prep(self, key: str, text: str) -> None:
        """Record a preparation step for the log."""
        self.state.setdefault("prep", {})[key] = {"at": stamp(), "text": text}
        self.save()

    # ---- sessions ------------------------------------------------------------------------

    def _context(
        self, key: str, *, mobile: bool = False, width: int | None = None
    ) -> BrowserContext:
        if key not in self.contexts:
            viewport = dict(MOBILE if mobile else VIEWPORT)
            if width:
                viewport = {"width": width, "height": 400}
            self.contexts[key] = self.browser.new_context(
                base_url=self.stack.web,
                locale="nl-NL",
                timezone_id="Europe/Amsterdam",
                viewport=viewport,
                device_scale_factor=SCALE,
                is_mobile=mobile,
                has_touch=mobile,
                color_scheme="light",
                reduced_motion="reduce",
            )
        return self.contexts[key]

    def staff(self, user: str) -> Page:
        """Return a page signed in as development user ``user`` (through Keycloak)."""
        if user not in self.pages:
            page = self._context(f"staff:{user}").new_page()
            page.goto("/admin/")
            if page.url.startswith(self.stack.keycloak):
                page.locator("#username").fill(user)
                page.locator("#password").fill(realm_password(user))
                page.locator("#password").press("Enter")
                page.wait_for_url(
                    re.compile(re.escape(self.stack.web) + r"/admin.*"), timeout=30000
                )
            self.pages[user] = page
        page = self.pages[user]
        page.set_viewport_size(self.staff_viewport)
        return page

    @contextmanager
    def viewport_for(self, cid: str) -> Iterator[None]:
        """Run the step ``cid`` in its window: ``WIDE`` for an id of ``WIDE_IDS``, else unchanged.

        The steps of ``WIDE_IDS`` open their page through ``staff()``, which sets the window
        before the page loads; afterwards the staff pages get ``VIEWPORT`` back.
        """
        if cid not in WIDE_IDS:
            yield
            return
        self.staff_viewport = dict(WIDE)
        try:
            yield
        finally:
            self.staff_viewport = dict(VIEWPORT)
            for page in self.pages.values():
                if page.viewport_size == WIDE:
                    page.set_viewport_size(VIEWPORT)

    def participant(self, who: str, *, mobile: bool = False, fresh: bool = False) -> Page:
        """Return the browser of a made-up participant (one context per person)."""
        key = f"participant:{who}{':mobile' if mobile else ''}"
        if fresh and key in self.contexts:
            self.contexts.pop(key).close()
            self.pages.pop(key, None)
        if key not in self.pages:
            self.pages[key] = self._context(key, mobile=mobile).new_page()
        page = self.pages[key]
        page.set_viewport_size(MOBILE if mobile else VIEWPORT)
        return page

    def mail_page(self) -> Page:
        """Return a page of 700 CSS pixels wide for rendering email HTML."""
        key = "mail"
        if key not in self.pages:
            self.pages[key] = self._context(key, width=EMAIL_WIDTH).new_page()
        return self.pages[key]

    # ---- capture -------------------------------------------------------------------------

    def shot(
        self,
        page: Page,
        cid: str,
        *,
        role: str,
        visible: str,
        how: Shot | None = None,
        notes: list[str] | None = None,
        address: str | None = None,
    ) -> None:
        """Capture ``page`` as ``img/<cid>.png``, cover the product name, record the evidence."""
        how = how or Shot()
        _settle(page)
        old = page.viewport_size or VIEWPORT
        stretched = how.mode in ("top", "full", "region", "email")
        try:
            if stretched:
                _stretch(page)
            facts = page.evaluate(PAGE_FACTS_JS, list(NO_VIDEO_PHRASES))
            boxes = page.evaluate(DETECT_JS)
            for locator, what in how.cover:
                for element in locator.all():
                    box = element.bounding_box()
                    if box and element.is_visible():
                        text = re.sub(r"\s+", " ", element.inner_text()).strip()[:140]
                        boxes.append(
                            {
                                "kind": "contactcenterblok",
                                "label": what,
                                "context": text,
                                "x": box["x"],
                                "y": box["y"],
                                "w": box["width"],
                                "h": box["height"],
                            }
                        )
            for locator, what in how.cover_versions:
                for element in locator.all():
                    box = element.bounding_box()
                    if box and element.is_visible():
                        text = re.sub(r"\s+", " ", element.inner_text()).strip()[:140]
                        boxes.append(
                            {
                                "kind": "versieveld",
                                "label": what,
                                "context": text,
                                "x": box["x"],
                                "y": box["y"],
                                "w": box["width"],
                                "h": box["height"],
                            }
                        )
            for table, header, what in how.cover_columns:
                for cell in table.evaluate(COLUMN_JS, header):
                    boxes.append({"kind": "contactcenterkolom", "label": what, **cell})
            size = page.viewport_size or old
            clip = {
                "x": 0.0,
                "y": 0.0,
                "width": float(size["width"]),
                "height": float(size["height"]),
            }
            crops: list[str] = []
            if how.mode == "top" and how.until is not None:
                box = how.until.bounding_box()
                if box:
                    clip["height"] = min(clip["height"], box["y"] + box["height"] + how.margin)
                    crops.append(f"van boven tot onder {_describe(how.until)} (+{how.margin} px)")
            elif how.mode == "top":
                clip["height"] = min(clip["height"], float(VIEWPORT["height"]))
                crops.append("bovenste 800 px van de pagina")
            elif how.mode == "region" and how.target is not None:
                parts = [b for b in (t.bounding_box() for t in _all(how.target)) if b]
                if not parts:
                    raise RuntimeError(f"region {how.target} not on the page")
                box = _union(parts)
                if how.bottom is not None:
                    end = how.bottom.bounding_box()
                    if end:
                        box["height"] = end["y"] + end["height"] - box["y"]
                        crops.append(f"afgesneden onder {_describe(how.bottom)}")
                y0 = max(0.0, box["y"] - how.margin)
                y1 = min(clip["height"], box["y"] + box["height"] + how.margin)
                if how.full_width:
                    clip = {"x": 0.0, "y": y0, "width": clip["width"], "height": y1 - y0}
                else:
                    x0 = max(0.0, box["x"] - how.margin)
                    x1 = min(clip["width"], box["x"] + box["width"] + how.margin)
                    clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
                crops.append(f"uitsnede rond {_describe(how.target)} (marge {how.margin} px)")
            elif how.mode == "full":
                crops.append("hele pagina")
            elif how.mode == "email":
                bottom = page.evaluate(
                    "() => Math.ceil(Math.max(0, ...[...document.body.children]"
                    ".map(e => e.getBoundingClientRect().bottom)))"
                )
                clip["height"] = min(clip["height"], float(bottom + 16))
                crops.append(
                    f"hele bericht, {EMAIL_WIDTH} px breed, tot 16 px onder de laatste regel"
                )
            else:
                crops.append(f"venster {size['width']}×{size['height']}")
            if how.crop_env_bar and clip["y"] < 1:
                bar = page.locator(".env-bar").first
                if bar.count() and bar.is_visible():
                    bottom = page.evaluate(
                        "() => { const b = document.querySelector('.env-bar')"
                        ".getBoundingClientRect(); return Math.ceil(b.bottom); }"
                    )
                    if bottom > 0:
                        clip["y"] = float(bottom)
                        clip["height"] -= float(bottom)
                        crops.append(
                            f"omgevingsbalk 'Ontwikkelomgeving' (bovenste {bottom} px) weggesneden"
                        )
            png = page.screenshot(type="png", clip=clip, animations="disabled")
        finally:
            if stretched:
                page.set_viewport_size(old)
        image = Image.open(io.BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        redactions = []
        for box in boxes:
            x0 = (box["x"] - clip["x"] - PAD) * SCALE
            y0 = (box["y"] - clip["y"] - PAD) * SCALE
            x1 = (box["x"] + box["w"] - clip["x"] + PAD) * SCALE
            y1 = (box["y"] + box["h"] - clip["y"] + PAD) * SCALE
            if x1 <= 0 or y1 <= 0 or x0 >= image.width or y0 >= image.height:
                continue
            rect = [
                max(0, round(x0)),
                max(0, round(y0)),
                min(image.width, round(x1)),
                min(image.height, round(y1)),
            ]
            draw.rectangle(rect, fill=GREY)
            redactions.append(
                {
                    "wat": box["kind"],
                    "tekst": box["label"],
                    "context": box.get("context", ""),
                    "vak_px": rect,
                }
            )
        IMG.mkdir(parents=True, exist_ok=True)
        image.save(IMG / f"{cid}.png", optimize=True)
        url = address or _safe_url(page.url)
        record = {
            "id": cid,
            "status": "opgenomen",
            "rol": role,
            "adres": url,
            "tijd": stamp(),
            "zichtbaar": visible,
            "kop": facts.get("h1", []),
            "meldingen": facts.get("notices", []),
            "zonder_webex": facts.get("present", []),
            "bijsnijding": crops,
            "afdekkingen": redactions,
            "afmeting_px": [image.width, image.height],
            "notities": notes or [],
        }
        self.records[cid] = record
        self.save()
        print(f"  ok   {cid}  {image.width}x{image.height}  redactions={len(redactions)}  {url}")

    def email_shot(self, cid: str, message: dict[str, Any], *, visible: str) -> None:
        """Render a message's HTML part at 700 px and capture the message only."""
        page = self.mail_page()
        page.set_viewport_size({"width": EMAIL_WIDTH, "height": 400})
        page.set_content(message.get("HTML") or "<p>(geen HTML-deel)</p>", wait_until="load")
        address = f'Mailpit-bericht {message["ID"]} (HTML-deel), onderwerp "{message["Subject"]}"'
        self.shot(
            page,
            cid,
            role="deelnemer: e-mail zoals ontvangen (Mailpit, "
            + ", ".join(t["Address"] for t in message.get("To") or [])
            + ")",
            visible=visible,
            how=Shot(mode="email", crop_env_bar=False),
            address=address,
            notes=[
                f"Verzonden {_local(message.get('Date', ''))}; bijlagen: "
                + (
                    ", ".join(a.get("FileName", "?") for a in message.get("Attachments") or [])
                    or "geen"
                )
            ],
        )

    def skipped(self, cid: str, reason: str) -> None:
        """Record that an id was not captured, and why; an earlier capture of it stays as it is."""
        old = self.records.get(cid)
        if old and old.get("status") == "opgenomen" and (IMG / f"{cid}.png").exists():
            old.setdefault("notities", []).append(
                f"Latere poging ({stamp()}) niet opgenomen: {reason}; deze opname blijft staan."
            )
            self.save()
            print(f"  --   {cid}  niet opnieuw opgenomen (de eerdere opname blijft): {reason}")
            return
        self.records[cid] = {
            "id": cid,
            "status": "niet opgenomen",
            "reden": reason,
            "tijd": stamp(),
        }
        self.save()
        print(f"  --   {cid}  niet opgenomen: {reason}")


def _local(stamp_utc: str) -> str:
    """Return Mailpit's ISO time in the group's time zone, as the log writes times."""
    try:
        return (
            dt.datetime.fromisoformat(stamp_utc.replace("Z", "+00:00"))
            .astimezone(TZ)
            .strftime("%Y-%m-%d %H:%M:%S %Z")
        )
    except ValueError:
        return stamp_utc or "?"


def _all(target: Locator | list[Locator]) -> list[Locator]:
    """Return ``target`` as a list of locators."""
    return target if isinstance(target, list) else [target]


DESCRIBE_JS = (
    r"""
(e) => {
  const head = e.matches('h1,h2,h3,h4,caption,legend,label,th,dt,summary,button,a')
    ? e : e.querySelector('h1,h2,h3,h4,caption,legend,label,dt,summary');
  const text = ((head ? head.innerText : e.innerText) || '')"""
    r""".replace(/\s+/g, ' ').trim().slice(0, 60);
  const what = {SECTION: 'onderdeel', FORM: 'formulier', TABLE: 'tabel', """
    r"""TBODY: 'tijdslot', DETAILS: 'uitleg',
                BUTTON: 'knop', DIV: 'blok', TR: 'rij', DD: 'waarde', A: 'link'}[e.tagName] """
    r"""|| e.tagName.toLowerCase();
  return `${what} "${text}"`;
}
"""
)


def _describe(locator: Locator | list[Locator]) -> str:
    """Return a readable name for a crop target: its kind and its heading or first words."""
    first = (locator[0] if isinstance(locator, list) else locator).first
    try:
        return re.sub(
            r"(?i)openplanner", "[productnaam]", first.evaluate(DESCRIBE_JS, timeout=3000)
        )
    except PlaywrightTimeout:
        return "element"


def _safe_url(url: str) -> str:
    """Return ``url`` with any booking-link token blanked."""
    return TOKEN_IN_PATH.sub("/b/<token>", url)


# --------------------------------------------------------------------------------------------
# Participant actions (real paths: the page, the link in the email)
# --------------------------------------------------------------------------------------------


def h1(page: Page) -> str:
    """Return the first level-1 heading of the page."""
    heading = page.get_by_role("heading", level=1).first
    return heading.inner_text().strip() if heading.count() else ""


def wait_h1(page: Page, text: str, timeout: int = 20000) -> None:
    """Wait until the page's level-1 heading reads ``text``."""
    page.get_by_role("heading", level=1, name=text).first.wait_for(timeout=timeout)


def pick_day(page: Page, day: str) -> None:
    """Click a free day in the slot picker and wait for its times."""
    link = page.locator(f'a.calendar__day[data-day="{day}"]')
    if not link.count():
        raise RuntimeError(f"day {day} is not free in the calendar on {page.url}")
    link.click()
    deadline = time.time() + 20
    while time.time() < deadline:
        busy = page.locator('#times[aria-busy="true"]').count()
        url_day = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query).get("day", [""])[0]
        if url_day == day and not busy and page.locator("#times .time-button").count():
            return
        page.wait_for_timeout(200)
    raise RuntimeError(f"the times of {day} did not appear")


def time_button(page: Page, label: str) -> Locator:
    """Return the time button that reads ``label`` (local time)."""
    button = page.locator("#times .time-button").filter(
        has_text=re.compile(rf"^\s*{re.escape(label)}\s*$")
    )
    if not button.count():
        shown = page.locator("#times .time-button").all_inner_texts()
        raise RuntimeError(f"no free time {label} (free: {', '.join(t.strip() for t in shown)})")
    return button.first


def fill_contact(page: Page, who: str, email: str | None = None) -> None:
    """Fill the participant's names and email address on the form."""
    first, last, address = PEOPLE[who]
    page.get_by_label("Voornaam").fill(first)
    page.get_by_label("Achternaam").fill(last)
    page.get_by_label("E-mailadres").fill(email if email is not None else address)


def submit_booking(page: Page) -> None:
    """Press "Afspraak maken" and wait for the request to confirm by email."""
    page.get_by_role("button", name="Afspraak maken").click()
    wait_h1(page, "Bevestig uw afspraak")


def confirm_by_link(run: Run, page: Page, who: str, seen: set[str], key: str) -> dict[str, Any]:
    """Open the link of the confirm request email and record the reference; return the email."""
    email = PEOPLE[who][2]
    request = run.mail.wait(email, SUBJECT["confirm_request"], seen)
    confirm = next((u for u in run.mail.links(request) if u.endswith("/confirm")), None)
    if not confirm:
        raise RuntimeError("the confirm request holds no /confirm link")
    page.goto(confirm)
    wait_h1(page, "Afspraak bevestigen")
    reference = urllib.parse.urlparse(page.url).path.split("/")[3]
    run.bookings.setdefault(key, {}).update(
        {"reference": reference, "confirm_request_mail": request["ID"]}
    )
    run.save()
    return request


def book_and_confirm(run: Run, key: str, who: str, day: str, label: str) -> None:
    """Book ``day`` at ``label`` on the group page as ``who`` and confirm by the emailed link."""
    if run.bookings.get(key, {}).get("status") == "confirmed":
        print(f"  ..   {key}: al geboekt en bevestigd ({run.bookings[key]['reference']})")
        return
    email = PEOPLE[who][2]
    seen = run.mail.ids(email)
    page = run.participant(who, fresh=True)
    page.goto(f"/g/{GROUP}")
    wait_h1(page, GROUP_NAME)
    pick_day(page, day)
    time_button(page, label).click()
    wait_h1(page, "Uw gegevens")
    fill_contact(page, who)
    submit_booking(page)
    run.bookings[key] = {
        "who": who,
        "day": day,
        "time": label,
        "status": "pending",
        "booked_at": stamp(),
    }
    run.save()
    confirm_by_link(run, page, who, seen, key)
    page.get_by_role("button", name="Afspraak bevestigen").click()
    page.get_by_role("status").filter(has_text="Uw afspraak is bevestigd").first.wait_for(
        timeout=20000
    )
    confirmation = run.mail.wait(email, SUBJECT["confirmation"], seen)
    run.bookings[key].update(
        {"status": "confirmed", "confirmation_mail": confirmation["ID"], "confirmed_at": stamp()}
    )
    run.save()
    print(
        f"  ..   {key}: {PEOPLE[who][0]} {day} {label} geboekt en bevestigd via de e-maillink "
        f"({run.bookings[key]['reference']})"
    )


def open_manage_link(run: Run, key: str, *, mail_key: str = "confirmation_mail") -> Page:
    """Open the plain management link of a booking's email in the participant's browser."""
    booking = run.booking(key)
    message = run.mail.message(booking[mail_key])
    link = next((u for u in run.mail.links(message) if u.count("/") == 4), None)
    if not link:
        raise RuntimeError(f"email {message['Subject']!r} holds no management link")
    page = run.participant(booking["who"], fresh=True)
    page.goto(link)
    wait_h1(page, "Uw afspraak")
    return page


# --------------------------------------------------------------------------------------------
# Story state helpers
# --------------------------------------------------------------------------------------------


def today_iso() -> str:
    """Return today's date in the group's time zone."""
    return now_local().date().isoformat()


def next_weekday(start: dt.date, n: int = 1) -> dt.date:
    """Return the ``n``-th weekday after ``start``."""
    day = start
    while n:
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def story_days(run: Run) -> dict[str, str]:
    """Return the two weekdays of the story, fixed at the first run."""
    days = run.state.setdefault("days", {})
    if not days:
        today = now_local().date()
        days["monday"] = next_weekday(today, 1).isoformat()
        days["tuesday"] = next_weekday(today, 2).isoformat()
        run.save()
    return days


def dutch_date(iso: str) -> str:
    """Return ``YYYY-MM-DD`` as the portal's ``dd-mm-jjjj``."""
    year, month, day = iso.split("-")
    return f"{day}-{month}-{year}"


def agenda_row(page: Page, first_name: str, time_label: str) -> Locator:
    """Return the agenda row of ``first_name`` whose header starts at ``time_label``."""
    row = (
        page.get_by_role("row")
        .filter(has_text=first_name)
        .filter(has=page.locator("th", has_text=re.compile(rf"^{re.escape(time_label)}–")))
    )
    row.first.wait_for(timeout=15000)
    return row.first


def form_of(locator: Locator) -> Locator:
    """Return the form that holds ``locator``."""
    return locator.locator("xpath=ancestor::form[1]")


# --------------------------------------------------------------------------------------------
# The steps, in story order
# --------------------------------------------------------------------------------------------

STEPS: list[tuple[str, Callable[[Run], None]]] = []


def step(name: str) -> Callable[[Callable[[Run], None]], Callable[[Run], None]]:
    """Register a step (an id of the list, or a story action ``s-...``) in story order."""

    def register(fn: Callable[[Run], None]) -> Callable[[Run], None]:
        STEPS.append((name, fn))
        return fn

    return register


# ---- Voorbereiding ------------------------------------------------------------------------


@step("s-voorbereiding-1-eigen-wachtrij")
def prep_queue(run: Run) -> None:
    """Voorbereiding 1: as orgadmin, put group demo on the own queue (record what was there)."""
    page = run.staff("orgadmin")
    page.goto(f"/admin/groups/{GROUP}/settings")
    select = page.get_by_label("Hoe afspraken bij medewerkers komen")
    before = select.evaluate("s => s.options[s.selectedIndex].text")
    visible_from = page.get_by_label(re.compile(r"^Zichtbaar in de wachtrij")).input_value()
    run.state["queue_visible_minutes"] = int(visible_from)
    if before.startswith("Eigen wachtrij"):
        run.note_prep(
            "1",
            f'Groep `demo` stond al op de eigen wachtrij ("{before}"); niets gewijzigd. Zichtbaar '
            f"in de wachtrij: {visible_from} minuten vooraf.",
        )
        return
    value = select.evaluate(
        "s => [...s.options].find(o => o.text.startsWith('Eigen wachtrij')).value"
    )
    select.select_option(value)
    form_of(select).get_by_role("button", name="Opslaan").click()
    page.wait_for_load_state("load")
    page.goto(f"/admin/groups/{GROUP}/settings")
    after = page.get_by_label("Hoe afspraken bij medewerkers komen").evaluate(
        "s => s.options[s.selectedIndex].text"
    )
    if not after.startswith("Eigen wachtrij"):
        raise RuntimeError(f"the group still says {after!r}")
    run.note_prep(
        "1",
        'Als `orgadmin`, **Groepen → demo → Instellingen**, veld "Hoe afspraken bij medewerkers '
        f'komen": stond op "{_mask_name(before)}", gezet op "{_mask_name(after)}" en opgeslagen. '
        f'"Zichtbaar in de wachtrij" bleef {visible_from} minuten vooraf (standaard).',
    )


def _mask_name(text: str) -> str:
    """Return ``text`` with the product name replaced by ``[productnaam]``."""
    return re.sub(r"(?i)openplanner", "[productnaam]", text)


@step("s-voorbereiding-2-voettekst-uit")
def prep_footer(run: Run) -> None:
    """Voorbereiding 2: as super, switch off the product footer (PRODUCT_FOOTER, class D)."""
    page = run.staff("super")
    page.goto(f"/admin/organisations/{ORG}/settings")
    select = page.get_by_label(re.compile(r"Afspraken via"))
    before = select.evaluate("s => s.options[s.selectedIndex].text")
    if before != "Uit":
        select.select_option(label="Uit")
        form_of(select).get_by_role("button", name="Opslaan").click()
        page.wait_for_load_state("load")
    page.goto(f"/admin/organisations/{ORG}/settings")
    after = page.get_by_label(re.compile(r"Afspraken via")).evaluate(
        "s => s.options[s.selectedIndex].text"
    )
    if after != "Uit":
        raise RuntimeError(f"PRODUCT_FOOTER still {after!r}")
    deadline = time.time() + 30
    check = run.participant("controle", fresh=True)
    while time.time() < deadline:
        check.goto(f"/g/{GROUP}")
        if not check.locator(".product-footer").count():
            break
        check.wait_for_timeout(2000)
    else:
        raise RuntimeError("the footer still shows on /g/demo")
    run.note_prep(
        "2",
        "Als `super`, **Organisaties → Demo-acme → Overzicht → Instellingen**, onderdeel Weergave, "
        'instelling `PRODUCT_FOOTER` (label "Toon "Afspraken via [productnaam]" onderaan", klasse '
        f'D): stond op "{before}", gezet op "{after}" en opgeslagen. Daarna toont `/g/demo` geen '
        "voettekst meer, en het HTML-deel van de e-mails ook niet (zelfde instelling).",
    )


@step("s-voorbereiding-2b-onderwerpvraag-uit")
def prep_form_question(run: Run) -> None:
    """Extra preparation: switch off the seed's subject question, whose options are sector words.

    The demo seed asks "Waar gaat uw vraag over?" with options that name a sector; the
    editorial rules (redactiestatuut §2.6) forbid sector words. Like the footer, a real setting
    switches it off: the button "Uitzetten" on the booking form's questions.
    """
    page = run.staff("orgadmin")
    page.goto(f"/admin/groups/{GROUP}/settings/form")
    button = page.get_by_role("button", name="Waar gaat uw vraag over? uitzetten")
    if not button.count():
        run.note_prep("2b", 'De vraag "Waar gaat uw vraag over?" stond al uit; niets gewijzigd.')
        return
    button.click()
    page.wait_for_load_state("load")
    confirm = page.get_by_role("button", name=re.compile(r"^(Uitzetten|Ja)"))
    if confirm.count() and "settings/form" in page.url and confirm.first.is_visible():
        confirm.first.click()
        page.wait_for_load_state("load")
    page.goto(f"/admin/groups/{GROUP}/settings/form")
    if page.get_by_role("button", name="Waar gaat uw vraag over? uitzetten").count():
        raise RuntimeError("the question is still on")
    run.note_prep(
        "2b",
        "Extra, niet in de lijst: als `orgadmin`, **Groepen → demo → Instellingen → "
        'Boekingsformulier**, bij de eigen vraag "Waar gaat uw vraag over?" (keuzelijst uit de '
        "demovulling) op **Uitzetten** gedrukt. Reden: de antwoordopties van de demovraag zijn "
        "sectorwoorden (redactiestatuut §2.6); net als bij de voettekst haalt een echte instelling "
        'ze weg in plaats van een balk. De vraag "Ik wil graag een tolk" bleef aan.',
    )


@step("pl-05-afwijkende-bezetting")
def pl_05(run: Run) -> None:
    """Voorbereiding 3 and pl-05: as groupadmin, today a staffing exception of its own block."""
    page = run.staff("groupadmin")
    block = run.state.get("today_block")
    if not block:
        start = now_local() + dt.timedelta(minutes=65)
        minutes = (start.minute // 30 + 1) * 30 if start.minute % 30 else start.minute
        start = start.replace(minute=0, second=0, microsecond=0) + dt.timedelta(minutes=minutes)
        end = start + dt.timedelta(hours=3)
        if end.date() != start.date() or end.hour == 0:
            end = start.replace(hour=23, minute=30)
        if end - start < dt.timedelta(hours=1):
            raise Skip(
                "te laat op de dag voor een tijdblok van vandaag; draai het verhaal eerder op de "
                "dag"
            )
        block = {
            "date": start.date().isoformat(),
            "from": start.strftime("%H:%M"),
            "to": end.strftime("%H:%M"),
        }
        page.goto(f"/admin/groups/{GROUP}/exceptions")
        form = page.get_by_role("form", name="Afwijkende bezetting toevoegen")
        form.get_by_label("Datum").fill(dutch_date(block["date"]))
        form.get_by_role("radio", name="Eigen tijdblok").check()
        form.get_by_label(re.compile(r"^Van")).fill(block["from"])
        form.get_by_label(re.compile(r"^Tot")).fill(block["to"])
        form.get_by_label(re.compile(r"^Aantal medewerkers")).fill("2")
        form.get_by_label("Aanbod").select_option(label="Afspraken")
        form.get_by_role("button", name="Afwijkende bezetting toevoegen").click()
        page.wait_for_load_state("load")
        run.state["today_block"] = block
        run.note_prep(
            "3",
            "Als `groupadmin`, **Groepen → demo → Planning → Afwijkende bezetting**: datum "
            f'{dutch_date(block["date"])}, soort "Eigen tijdblok", '
            f'{block["from"]}–{block["to"]}, 2 medewerkers, aanbod "Afspraken", label leeg; '
            "**Afwijkende bezetting toevoegen**. Dit is ook opname pl-05.",
        )
    page.goto(f"/admin/groups/{GROUP}/exceptions")
    listing = page.get_by_role("region", name="Komende afwijkende bezetting")
    if not listing.get_by_text(block["from"]).count():
        raise RuntimeError("today's exception is not listed")
    run.shot(
        page,
        "pl-05-afwijkende-bezetting",
        role=ROLES["groupadmin"],
        visible=(
            'Planning → Afwijkende bezetting van de groep: de lijst "Komende afwijkende bezetting" '
            f"met de afwijking van vandaag ({dutch_date(block['date'])}, eigen tijdblok "
            f"{block['from']}–{block['to']}, 2 medewerkers, Afspraken) en daaronder het lege "
            'formulier "Afwijkende bezetting toevoegen".'
        ),
        how=Shot(mode="full"),
    )


@step("s-boeking-vandaag-ahmed")
def book_today(run: Run) -> None:
    """Ahmed el Amrani books today's first slot of the exception; confirms by the emailed link."""
    block = run.state.get("today_block")
    if not block:
        raise Skip("geen tijdblok van vandaag (pl-05 niet gedraaid)")
    book_and_confirm(run, "ahmed-vandaag", "ahmed", block["date"], block["from"])


# ---- Deelnemers: boeken, bevestigen --------------------------------------------------------


@step("bb-01-groepspagina")
def bb_01(run: Run) -> None:
    """bb-01: /g/demo with the title and the month calendar."""
    page = run.participant("sanne", fresh=True)
    page.goto(f"/g/{GROUP}")
    wait_h1(page, GROUP_NAME)
    page.locator("#times .time-button").first.wait_for(timeout=15000)
    run.shot(
        page,
        "bb-01-groepspagina",
        role="deelnemer, niet ingelogd (Sanne Jansen opent de pagina)",
        visible=(
            'De groepspagina: organisatienaam en taalkeuze bovenaan, de titel "Demo: '
            'videogesprek", de stappen Dag en tijd · Uw gegevens · Bevestigd, "Eerst mogelijk" met '
            'de knop "Kies dit moment", de maandkalender met de vrije dagen (vandaag door de '
            "afwijkende bezetting, en de werkdagen) en rechts de vrije tijden van de eerste vrije "
            "dag. Geen voettekst met de productnaam (instelling uit)."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-02-tijden")
def bb_02(run: Run) -> None:
    """bb-02: the same page after choosing a weekday: its free times."""
    days = story_days(run)
    page = run.participant("sanne")
    if f"/g/{GROUP}" not in page.url:
        page.goto(f"/g/{GROUP}")
    pick_day(page, days["monday"])
    count = page.locator("#times .time-button").count()
    run.shot(
        page,
        "bb-02-tijden",
        role="deelnemer, niet ingelogd (Sanne Jansen)",
        visible=(
            f"Dezelfde pagina na het kiezen van {dutch_date(days['monday'])} in de kalender: "
            f"rechts de {count} vrije tijden van die dag, in Ochtend en Middag."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-03-formulier")
def bb_03(run: Run) -> None:
    """bb-03: the empty form "Uw gegevens" with the chosen moment beside it."""
    days = story_days(run)
    page = run.participant("sanne")
    if not page.locator("#times .time-button").count():
        page.goto(f"/g/{GROUP}")
        pick_day(page, days["monday"])
    time_button(page, "10:00").click()
    wait_h1(page, "Uw gegevens")
    run.shot(
        page,
        "bb-03-formulier",
        role="deelnemer, niet ingelogd (Sanne Jansen)",
        visible=(
            'Het lege formulier "Uw gegevens" (voornaam, achternaam, e-mailadres, telefoonnummer '
            'optioneel, de eigen vraag "Ik wil graag een tolk") met het gekozen moment '
            f"({dutch_date(days['monday'])}, 10:00) ernaast."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-04-foutmelding")
def bb_04(run: Run) -> None:
    """bb-04: the form after "Afspraak maken" with an invalid email address."""
    page = run.participant("sanne")
    if h1(page) != "Uw gegevens":
        raise Skip("het formulier van bb-03 staat niet open")
    fill_contact(page, "sanne", email="sanne.jansen.voorbeeld.nl")
    page.get_by_role("button", name="Afspraak maken").click()
    page.get_by_role("alert").first.wait_for(timeout=15000)
    run.shot(
        page,
        "bb-04-foutmelding",
        role="deelnemer, niet ingelogd (Sanne Jansen)",
        visible=(
            'Het formulier na "Afspraak maken" met het ongeldige e-mailadres '
            '"sanne.jansen.voorbeeld.nl" (zonder @): bovenaan "Er is een probleem" met een link '
            "naar het veld, het veld E-mailadres gemarkeerd met de foutmelding; voor- en "
            "achternaam blijven ingevuld."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-05-controleer-mail")
def bb_05(run: Run) -> None:
    """bb-05: "Bevestig uw afspraak" after a valid form."""
    days = story_days(run)
    page = run.participant("sanne")
    email = PEOPLE["sanne"][2]
    run.state["seen_sanne"] = sorted(run.mail.ids(email))
    page.get_by_label("E-mailadres").fill(email)
    submit_booking(page)
    run.bookings["sanne-ma"] = {
        "who": "sanne",
        "day": days["monday"],
        "time": "10:00",
        "status": "pending",
        "booked_at": stamp(),
    }
    run.save()
    run.shot(
        page,
        "bb-05-controleer-mail",
        role="deelnemer, niet ingelogd (Sanne Jansen)",
        visible=(
            'De pagina "Bevestig uw afspraak" met de melding "We hebben u een e-mail gestuurd": de '
            "afspraak is gemaakt en wacht op bevestiging via de link in de e-mail."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-06-mail-bevestigen")
def bb_06(run: Run) -> None:
    """bb-06: the email "Bevestig uw afspraak met …" (HTML part)."""
    email = PEOPLE["sanne"][2]
    seen = set(run.state.get("seen_sanne", []))
    known = run.bookings.get("sanne-ma", {}).get("confirm_request_mail")
    message = (
        run.mail.message(known) if known else run.mail.wait(email, SUBJECT["confirm_request"], seen)
    )
    run.bookings.setdefault("sanne-ma", {})["confirm_request_mail"] = message["ID"]
    run.save()
    run.email_shot(
        "bb-06-mail-bevestigen",
        message,
        visible=(
            f'De e-mail "{message["Subject"]}" aan Sanne Jansen, het HTML-deel: aanhef, uitleg, '
            "wanneer en team, en de link om de afspraak te bevestigen (als linktekst; de link zelf "
            "staat niet in beeld)."
        ),
    )


@step("bb-07-bevestigpagina")
def bb_07(run: Run) -> None:
    """bb-07: the page behind the link: button "Afspraak bevestigen"."""
    booking = run.booking("sanne-ma")
    message = run.mail.message(booking["confirm_request_mail"])
    confirm = next((u for u in run.mail.links(message) if u.endswith("/confirm")), None)
    if not confirm:
        raise RuntimeError("no /confirm link in the email")
    page = run.participant("sanne")
    page.goto(confirm)
    wait_h1(page, "Afspraak bevestigen")
    booking["reference"] = urllib.parse.urlparse(page.url).path.split("/")[3]
    run.save()
    run.shot(
        page,
        "bb-07-bevestigpagina",
        role="deelnemer, via de link in de e-mail (Sanne Jansen)",
        visible=(
            'De pagina achter de bevestigingslink: kop "Afspraak bevestigen", het moment en de '
            'knop "Afspraak bevestigen". Het adres bevat geen token meer '
            "(/b/m/<afspraaknummer>/confirm)."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-08-bevestigd")
def bb_08(run: Run) -> None:
    """bb-08: "Uw afspraak is bevestigd"."""
    page = run.participant("sanne")
    if h1(page) != "Afspraak bevestigen":
        raise Skip("de bevestigpagina van bb-07 staat niet open")
    page.get_by_role("button", name="Afspraak bevestigen").click()
    page.get_by_role("status").filter(has_text="Uw afspraak is bevestigd").first.wait_for(
        timeout=20000
    )
    run.bookings["sanne-ma"].update({"status": "confirmed", "confirmed_at": stamp()})
    run.save()
    run.shot(
        page,
        "bb-08-bevestigd",
        role="deelnemer, via de link in de e-mail (Sanne Jansen)",
        visible=(
            'Na "Afspraak bevestigen": de melding "Uw afspraak is bevestigd" boven de '
            "afspraakpagina."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-09-mail-bevestiging")
def bb_09(run: Run) -> None:
    """bb-09: the email "Bevestiging van uw videogesprek op …"."""
    booking = run.booking("sanne-ma")
    email = PEOPLE["sanne"][2]
    if booking.get("confirmation_mail"):
        message = run.mail.message(booking["confirmation_mail"])
    else:
        seen = set(run.state.get("seen_sanne", [])) | {booking["confirm_request_mail"]}
        message = run.mail.wait(email, SUBJECT["confirmation"], seen)
        booking["confirmation_mail"] = message["ID"]
        run.save()
    run.email_shot(
        "bb-09-mail-bevestiging",
        message,
        visible=(
            f'De e-mail "{message["Subject"]}" aan Sanne Jansen, het HTML-deel: wanneer, team en '
            "de link om de afspraak te bekijken of te wijzigen. Zonder Webex Meetings staat er "
            "geen link om deel te nemen of een proefgesprek te doen."
        ),
    )


@step("bb-10-uw-afspraak")
def bb_10(run: Run) -> None:
    """bb-10: "Uw afspraak" opened from the confirmation email."""
    page = open_manage_link(run, "sanne-ma")
    run.shot(
        page,
        "bb-10-uw-afspraak",
        role="deelnemer, via de link in de bevestigingsmail (Sanne Jansen)",
        visible=(
            'De pagina "Uw afspraak": moment, groep, status en de links "Afspraak verplaatsen" en '
            '"Afspraak annuleren".'
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("s-boeking-maandag-pieter")
def book_pieter_monday(run: Run) -> None:
    """Pieter de Bakker books a weekday afternoon (cancelled later by himself)."""
    book_and_confirm(run, "pieter-ma", "pieter", story_days(run)["monday"], "14:00")


@step("s-boeking-maandag-ahmed")
def book_ahmed_monday(run: Run) -> None:
    """Ahmed el Amrani books the same weekday at 09:00 (stays confirmed for the agenda)."""
    book_and_confirm(run, "ahmed-ma", "ahmed", story_days(run)["monday"], "09:00")


# ---- Verplaatsen en annuleren ----------------------------------------------------------------


@step("bb-11-verplaatsen")
def bb_11(run: Run) -> None:
    """bb-11: "Afspraak verplaatsen" with the calendar."""
    page = open_manage_link(run, "sanne-ma")
    page.get_by_role("link", name="Afspraak verplaatsen").click()
    wait_h1(page, "Afspraak verplaatsen")
    page.locator("a.calendar__day").first.wait_for(timeout=15000)
    run.shot(
        page,
        "bb-11-verplaatsen",
        role="deelnemer, via de link in de bevestigingsmail (Sanne Jansen)",
        visible=(
            'De pagina "Afspraak verplaatsen" met de huidige afspraak en dezelfde kalender met '
            "vrije dagen en tijden als bij het boeken."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-12-nieuw-moment")
def bb_12(run: Run) -> None:
    """bb-12: "Nieuw moment bevestigen" with the old and the new moment."""
    days = story_days(run)
    page = run.participant("sanne")
    if h1(page) != "Afspraak verplaatsen":
        raise Skip("de verplaatspagina van bb-11 staat niet open")
    pick_day(page, days["monday"])
    time_button(page, "11:30").click()
    wait_h1(page, "Nieuw moment bevestigen")
    run.shot(
        page,
        "bb-12-nieuw-moment",
        role="deelnemer, via de link in de bevestigingsmail (Sanne Jansen)",
        visible=(
            f'"Nieuw moment bevestigen": het oude moment ({dutch_date(days["monday"])} 10:00) en '
            f'het nieuwe ({dutch_date(days["monday"])} 11:30), met de knop "Verplaatsen".'
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-13-verplaatst")
def bb_13(run: Run) -> None:
    """bb-13: "Uw afspraak is verplaatst"."""
    page = run.participant("sanne")
    if h1(page) != "Nieuw moment bevestigen":
        raise Skip("de bevestiging van het nieuwe moment (bb-12) staat niet open")
    email = PEOPLE["sanne"][2]
    run.state["seen_sanne_move"] = sorted(run.mail.ids(email))
    page.get_by_role("button", name="Verplaatsen").click()
    page.get_by_role("status").filter(has_text="Uw afspraak is verplaatst").first.wait_for(
        timeout=20000
    )
    reference = urllib.parse.urlparse(page.url).path.split("/")[3]
    old = run.bookings["sanne-ma"]
    old["status"] = "moved"
    run.bookings["sanne-ma-verplaatst"] = {
        "who": "sanne",
        "day": old["day"],
        "time": "11:30",
        "reference": reference,
        "status": "confirmed",
        "moved_from": old.get("reference"),
        "moved_at": stamp(),
    }
    run.save()
    run.shot(
        page,
        "bb-13-verplaatst",
        role="deelnemer, via de link in de bevestigingsmail (Sanne Jansen)",
        visible=(
            'Na "Verplaatsen": de melding "Uw afspraak is verplaatst" op de afspraakpagina, met '
            "het nieuwe moment en een nieuw afspraaknummer."
        ),
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-17-mail-verplaatst")
def bb_17(run: Run) -> None:
    """bb-17: the email "Uw videogesprek is verplaatst naar …"."""
    booking = run.booking("sanne-ma-verplaatst")
    if booking.get("change_mail"):
        message = run.mail.message(booking["change_mail"])
    else:
        message = run.mail.wait(
            PEOPLE["sanne"][2], SUBJECT["change"], set(run.state.get("seen_sanne_move", []))
        )
        booking["change_mail"] = message["ID"]
        run.save()
    run.email_shot(
        "bb-17-mail-verplaatst",
        message,
        visible=(
            f'De e-mail "{message["Subject"]}" aan Sanne Jansen, het HTML-deel: het nieuwe moment, '
            "het team en de link om de afspraak te bekijken of te wijzigen."
        ),
    )


@step("bb-14-annuleren")
def bb_14(run: Run) -> None:
    """bb-14: "Afspraak annuleren" with "Ja, annuleren" (Pieter's own booking)."""
    page = open_manage_link(run, "pieter-ma")
    page.get_by_role("link", name="Afspraak annuleren").click()
    wait_h1(page, "Afspraak annuleren")
    run.shot(
        page,
        "bb-14-annuleren",
        role="deelnemer, via de link in de bevestigingsmail (Pieter de Bakker)",
        visible='De pagina "Afspraak annuleren" met de afspraak en de knop "Ja, annuleren".',
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-15-geannuleerd")
def bb_15(run: Run) -> None:
    """bb-15: "Uw afspraak is geannuleerd"."""
    page = run.participant("pieter")
    if h1(page) != "Afspraak annuleren":
        raise Skip("de annuleerpagina van bb-14 staat niet open")
    run.state["seen_pieter_cancel"] = sorted(run.mail.ids(PEOPLE["pieter"][2]))
    page.get_by_role("button", name="Ja, annuleren").click()
    page.get_by_role("status").filter(has_text="Uw afspraak is geannuleerd").first.wait_for(
        timeout=20000
    )
    run.bookings["pieter-ma"].update({"status": "cancelled", "cancelled_at": stamp()})
    run.save()
    run.shot(
        page,
        "bb-15-geannuleerd",
        role="deelnemer, via de link in de bevestigingsmail (Pieter de Bakker)",
        visible='Na "Ja, annuleren": de melding "Uw afspraak is geannuleerd".',
        how=Shot(mode="full", crop_env_bar=False),
    )


@step("bb-18-mail-geannuleerd")
def bb_18(run: Run) -> None:
    """bb-18: the email "Uw videogesprek op … is geannuleerd"."""
    booking = run.booking("pieter-ma")
    if booking.get("cancellation_mail"):
        message = run.mail.message(booking["cancellation_mail"])
    else:
        message = run.mail.wait(
            PEOPLE["pieter"][2],
            SUBJECT["cancellation"],
            set(run.state.get("seen_pieter_cancel", [])),
        )
        booking["cancellation_mail"] = message["ID"]
        run.save()
    run.email_shot(
        "bb-18-mail-geannuleerd",
        message,
        visible=(
            f'De e-mail "{message["Subject"]}" aan Pieter de Bakker, het HTML-deel: welke afspraak '
            'is geannuleerd (wanneer, team) en de zin "U kunt altijd een nieuwe afspraak maken."; '
            "het HTML-deel heeft geen link."
        ),
    )


@step("bb-16-mobiel")
def bb_16(run: Run) -> None:
    """bb-16: /g/demo on a phone (390 wide)."""
    page = run.participant("mobiel", mobile=True, fresh=True)
    page.goto(f"/g/{GROUP}")
    wait_h1(page, GROUP_NAME)
    page.locator("#times .time-button").first.wait_for(timeout=15000)
    run.shot(
        page,
        "bb-16-mobiel",
        role="deelnemer, niet ingelogd, op een telefoon (390 × 844, schaal 2)",
        visible=(
            'De groepspagina op een telefoon: titel, stappen, "Eerst mogelijk" en de maandkalender '
            "onder elkaar (het eerste scherm)."
        ),
        how=Shot(mode="viewport", crop_env_bar=False),
    )


# ---- Tekort: twee boekingen in één tijdslot, de planner verlaagt de bezetting ------------------


@step("s-boeking-dinsdag-pieter")
def book_pieter_tuesday(run: Run) -> None:
    """Pieter de Bakker books the second weekday at 10:00."""
    book_and_confirm(run, "pieter-di", "pieter", story_days(run)["tuesday"], "10:00")


@step("s-boeking-dinsdag-sanne")
def book_sanne_tuesday(run: Run) -> None:
    """Sanne Jansen books the same slot, so it holds two confirmed bookings."""
    book_and_confirm(run, "sanne-di", "sanne", story_days(run)["tuesday"], "10:00")


@step("s-tekort-bezetting-verlagen")
def shortage(run: Run) -> None:
    """The planner lowers the staffing of that day to one agent: the slot gets a shortage."""
    if run.state.get("shortage"):
        return
    day = story_days(run)["tuesday"]
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/exceptions")
    form = page.get_by_role("form", name="Afwijkende bezetting toevoegen")
    form.get_by_label("Datum").fill(dutch_date(day))
    form.get_by_role("radio", name="Eigen tijdblok").check()
    form.get_by_label(re.compile(r"^Van")).fill("09:00")
    form.get_by_label(re.compile(r"^Tot")).fill("12:00")
    form.get_by_label(re.compile(r"^Aantal medewerkers")).fill("1")
    form.get_by_label("Aanbod").select_option(label="Afspraken")
    form.get_by_role("button", name="Afwijkende bezetting toevoegen").click()
    page.wait_for_load_state("load")
    run.state["shortage"] = {"day": day, "block": "09:00–12:00", "agents": 1, "at": stamp()}
    run.note_prep(
        "tekort",
        f"Verhaal, als `groupadmin`: **Planning → Afwijkende bezetting**, datum {dutch_date(day)}, "
        "eigen tijdblok 09:00–12:00 met 1 medewerker (aanbod Afspraken). Op die dag stonden om "
        "10:00 al twee bevestigde afspraken (Pieter de Bakker en Sanne Jansen); het tijdslot "
        "krijgt daardoor een tekort van 1.",
    )


@step("pl-13-tekort")
def pl_13(run: Run) -> None:
    """pl-13: a slot with "Tekort: 1" in the agenda."""
    page = run.staff("groupadmin")
    first = (
        run.state.get("agenda_day") or (run.state.get("shortage") or {}).get("day") or today_iso()
    )
    start = dt.date.fromisoformat(first)
    day, slot = None, None
    for offset in range(0, 22):
        candidate = (start + dt.timedelta(days=offset)).isoformat()
        page.goto(f"/admin/groups/{GROUP}/agenda?day={candidate}")
        page.locator("#agenda-heading").wait_for(timeout=15000)
        found = page.locator("tbody", has=page.locator("tr.agenda-slot--short"))
        if found.count():
            day, slot = candidate, found.first
            break
    if slot is None:
        raise Skip(f"geen tijdslot met een tekort gevonden in de 3 weken vanaf {first}")
    chip = slot.locator(".chip--danger").first.inner_text().strip()
    run.shot(
        page,
        "pl-13-tekort",
        role=ROLES["groupadmin"],
        visible=(
            f"Agenda van {dutch_date(day)}: het tijdslot "
            f'"{re.sub(r"\s+", " ", slot.locator("tr.agenda-slot--short").inner_text()).strip()}", '
            f'gemarkeerd met "{chip}", met de knoppen om de deelnemers om een nieuwe tijd te '
            "vragen of het tekort te accepteren."
        ),
        how=Shot(
            mode="top",
            until=slot,
            margin=4,
            cover_columns=[
                (page.locator("table.agenda-table"), "Contactcentertaak", "kolom Contactcentertaak")
            ],
        ),
    )


@step("pl-12-nieuwe-tijd")
def pl_12(run: Run) -> None:
    """pl-12: "Om nieuwe tijd vragen" (filled in, then sent: the booking waits for a new time)."""
    booking = run.booking("pieter-di")
    page = run.staff("groupadmin")
    if booking.get("status") != "waiting":
        page.goto(f"/admin/groups/{GROUP}/agenda?day={booking['day']}")
        agenda_row(page, "Pieter", "10:00").get_by_role(
            "link", name=re.compile(r"^Om nieuwe tijd vragen")
        ).click()
        page.get_by_role("heading", name=re.compile(r"^Om een nieuwe tijd vragen")).first.wait_for(
            timeout=15000
        )
        reason = page.get_by_label("Reden")
        if reason.count():
            tag = reason.first.evaluate("e => e.tagName")
            if tag == "SELECT":
                reason.first.select_option(
                    label=reason.first.evaluate(
                        "s => [...s.options].find(o => /^Te weinig/.test(o.text)).text"
                    )
                )
        radio = page.get_by_role("radio", name=re.compile(r"^Te weinig"))
        if radio.count():
            radio.first.check()
        run.shot(
            page,
            "pl-12-nieuwe-tijd",
            role=ROLES["groupadmin"],
            visible=(
                'Het formulier "Om een nieuwe tijd vragen" voor de afspraak van Pieter in het '
                "tijdslot met tekort: uitleg wat er gebeurt (plek komt vrij, bericht met reden en "
                'link, herinnering, vervallen), de reden "Te weinig medewerkers" gekozen, en de '
                'knop "Om een nieuwe tijd vragen".'
            ),
            how=Shot(mode="full"),
            notes=[
                'Daarna verstuurd met "Om een nieuwe tijd vragen"; de afspraak staat sindsdien in '
                "Wacht op nieuwe tijd (pl-17)."
            ],
        )
        page.get_by_role("button", name="Om een nieuwe tijd vragen").click()
        page.wait_for_load_state("load")
        booking.update({"status": "waiting", "rebook_asked_at": stamp()})
        run.save()
    else:
        print(
            "  ..   pl-12: de vraag om een nieuwe tijd is al verstuurd; formulier niet opnieuw op "
            "te nemen"
        )


@step("pl-17-wacht-op-nieuwe-tijd")
def pl_17(run: Run) -> None:
    """pl-17: the list "Wacht op nieuwe tijd"."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/agenda/waiting")
    run.shot(
        page,
        "pl-17-wacht-op-nieuwe-tijd",
        role=ROLES["groupadmin"],
        visible=(
            'De lijst "Wacht op nieuwe tijd" met de afspraak van Pieter: afspraaknummer, '
            "oorspronkelijke tijd, reden, vervaldatum en de acties Verplaatsen en Annuleren."
        ),
        how=Shot(mode="full"),
    )


@step("pl-10-verplaatsen")
def pl_10(run: Run) -> None:
    """pl-10: the planner's "Verplaatsen": choosing a slot (not carried out)."""
    booking = run.booking("sanne-di")
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/agenda?day={booking['day']}")
    agenda_row(page, "Sanne", "10:00").get_by_role("link", name=re.compile(r"^Verplaatsen")).click()
    page.get_by_role("heading", name=re.compile(r"^Afspraak .* verplaatsen")).first.wait_for(
        timeout=15000
    )
    chosen = ""
    select = page.get_by_label(re.compile(r"^Nieuw tijdslot"))
    if select.count() and select.first.evaluate("e => e.tagName") == "SELECT":
        options = select.first.evaluate("s => [...s.options].map(o => o.text)")
        candidates = [o for o in options if "11:00" in o] or [o for o in options if o.strip()]
        if candidates:
            chosen = candidates[0]
            select.first.select_option(label=chosen)
    else:
        radio = page.get_by_role("radio").filter(has_text=re.compile("11:00"))
        if radio.count():
            radio.first.check()
            chosen = "11:00"
    run.shot(
        page,
        "pl-10-verplaatsen",
        role=ROLES["groupadmin"],
        visible=(
            'Het formulier "Afspraak … verplaatsen" van de planner voor de afspraak van Sanne: de '
            "vrije tijdsloten van de groep"
        )
        + (f", gekozen: {chosen}" if chosen else "")
        + ". Niet uitgevoerd.",
        how=Shot(mode="full"),
        notes=["Niet verplaatst: de opname laat alleen het kiezen van een tijdslot zien."],
    )


@step("pl-11-annuleren")
def pl_11(run: Run) -> None:
    """pl-11: the planner's "Annuleren" with the fixed reasons (not carried out)."""
    booking = run.booking("sanne-di")
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/agenda?day={booking['day']}")
    agenda_row(page, "Sanne", "10:00").get_by_role("link", name=re.compile(r"^Annuleren")).click()
    page.get_by_role("heading", name=re.compile(r"^Afspraak .* annuleren")).first.wait_for(
        timeout=15000
    )
    run.shot(
        page,
        "pl-11-annuleren",
        role=ROLES["groupadmin"],
        visible=(
            'Het formulier "Afspraak … annuleren" van de planner: de vaste redenen (Te weinig '
            'medewerkers, Technische storing, Onvoorziene omstandigheden) en de knop "Afspraak '
            'annuleren". Niet uitgevoerd.'
        ),
        how=Shot(mode="full"),
        notes=["Niet geannuleerd: de opname laat alleen het formulier zien."],
    )


# ---- Agenda --------------------------------------------------------------------------------


@step("pl-07-agenda-dag")
def pl_07(run: Run) -> None:
    """pl-07: the agenda's day view with confirmed bookings."""
    day = run.state.get("agenda_day") or story_days(run)["monday"]
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/agenda?day={day}")
    page.locator("#agenda-heading").wait_for(timeout=15000)
    booked = page.locator("table.agenda-table tbody").filter(
        has=page.get_by_role("link", name=re.compile(r"^Details"))
    )
    if not booked.count():
        raise Skip(f"de agenda van {day} heeft geen afspraken")
    last = booked.last
    run.shot(
        page,
        "pl-07-agenda-dag",
        role=ROLES["groupadmin"],
        visible=(
            f"Agenda, dagoverzicht van {dutch_date(day)}: per tijdslot de bezetting en de "
            "afspraken met de deelnemer (voornaam), de videomeeting, of er een medewerker binnen "
            "is en de acties. "
            + (
                "Bevestigd: 09:00 Ahmed en 11:30 Sanne (verplaatst); met het label Geannuleerd: "
                "10:00 Sanne (het oude moment) en 14:00 Pieter."
                if day == story_days(run)["monday"]
                else ""
            )
        ),
        how=Shot(
            mode="top",
            until=last,
            margin=4,
            cover_columns=[
                (page.locator("table.agenda-table"), "Contactcentertaak", "kolom Contactcentertaak")
            ],
        ),
        notes=[
            f"Opgenomen in een venster van {WIDE['width']} × {WIDE['height']}: bij 1280 px is de "
            "tabel breder dan zijn kader en valt de laatste kolom (Details, Verplaatsen, Om nieuwe "
            "tijd vragen, Annuleren) deels weg achter de horizontale schuifbalk van de tabel."
        ],
    )


@step("pl-08-agenda-week")
def pl_08(run: Run) -> None:
    """pl-08: the week overview."""
    day = run.state.get("agenda_day") or story_days(run)["monday"]
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/agenda/week?week={day}")
    page.wait_for_load_state("load")
    noon = page.get_by_role("row").filter(
        has=page.locator("th", has_text=re.compile(r"^\s*11:30–"))
    )
    run.shot(
        page,
        "pl-08-agenda-week",
        role=ROLES["groupadmin"],
        visible=(
            f"Het weekoverzicht van de week van {dutch_date(day)} (de ochtend, 09:00–12:00): per "
            "dag en tijdslot de bezetting en het aantal afspraken als link naar die dag; dinsdag 1 "
            "medewerker door de afwijkende bezetting. Boven de tabel de melding dat elk tijdslot "
            "genoeg medewerkers heeft (het tekort van dinsdag is weg sinds de planner Pieter om "
            "een nieuwe tijd vroeg, pl-12)."
        ),
        how=Shot(mode="top", until=noon.first, margin=4) if noon.count() else Shot(mode="top"),
        notes=[
            f"Opgenomen in een venster van {WIDE['width']} × {WIDE['height']}: bij 1280 px loopt "
            "de tabel rechts buiten zijn kader en valt de kolom van zondag half weg (horizontaal "
            "schuiven)."
        ],
    )


@step("pl-09-afspraak-details")
def pl_09(run: Run) -> None:
    """pl-09: the details of a booking (history, background jobs)."""
    booking = run.booking("ahmed-ma")
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/agenda?day={booking['day']}")
    agenda_row(page, "Ahmed", booking["time"]).get_by_role(
        "link", name=re.compile(r"^Details")
    ).click()
    page.wait_for_load_state("load")
    jobs = page.get_by_role("region", name="Achtergrondtaken").first
    term = page.locator("dt").filter(has_text=re.compile(r"^\s*Contactcentertaak\s*$"))
    rows = jobs.locator("tbody tr").all_inner_texts()
    run.shot(
        page,
        "pl-09-afspraak-details",
        role=ROLES["groupadmin"],
        visible=(
            f"De details van de afspraak van Ahmed ({dutch_date(booking['day'])} "
            f"{booking['time']}): afspraaknummer, moment en soort, de stand van het videogesprek, "
            'de antwoorden op het formulier, het verloop ("Nog niets gebeurd.") en de '
            "achtergrondtaken: " + "; ".join(re.sub(r"\s+", " ", r).strip() for r in rows) + "."
        ),
        how=Shot(
            mode="top",
            until=jobs,
            cover=[
                (term, "rij Contactcentertaak (term)"),
                (term.locator("xpath=following-sibling::dd[1]"), "rij Contactcentertaak (waarde)"),
            ],
        ),
        notes=[
            "Het verloop toont de gebeurtenissen van het videogesprek; zonder Webex Meetings is "
            'het leeg ("Nog niets gebeurd.").',
            'Het laatste blok van de pagina, "Verzoeken aan Webex en Webex Contact Center", valt '
            "onder de uitsnede (afdekregel 2).",
        ],
    )


# ---- Medewerker: wachtrij ------------------------------------------------------------------


def queue_window(run: Run) -> tuple[dt.datetime, dt.datetime]:
    """Return when today's booking shows in the queue as "Nog niet begonnen" (before its start)."""
    booking = run.booking("ahmed-vandaag")
    start = dt.datetime.fromisoformat(f"{booking['day']}T{booking['time']}").replace(tzinfo=TZ)
    minutes = int(run.state.get("queue_visible_minutes", 15))
    return start - dt.timedelta(minutes=minutes) + dt.timedelta(seconds=30), start + dt.timedelta(
        minutes=8
    )


QUEUE_ROWS = "table.data-table tbody tr"


def queue_rows(run: Run) -> int:
    """Return how many bookings the agent sees in the group's queue right now."""
    page = run.staff("agent")
    page.goto(f"/admin/groups/{GROUP}/queue")
    page.get_by_role("heading", name="Wachtende afspraken").wait_for(timeout=15000)
    return page.locator(QUEUE_ROWS).count()


def need_queue(run: Run) -> None:
    """Pass when a booking is in the queue; else NotYet before the story's window, Skip after it."""
    if queue_rows(run):
        return
    if "ahmed-vandaag" not in run.bookings:
        raise Skip(
            "er staat nu geen afspraak in de wachtrij (een afspraak verschijnt daar 15 minuten "
            "voor de start)"
        )
    opens, closes = queue_window(run)
    now = now_local()
    if now < opens:
        raise NotYet(opens, f"de afspraak van vandaag staat pas vanaf {opens:%H:%M} in de wachtrij")
    if now > closes:
        raise Skip(
            f"de afspraak van vandaag is al begonnen (venster tot {closes:%H:%M}); boek een latere "
            "tijd van vandaag"
        )


@step("mw-01-overzicht")
def mw_01(run: Run) -> None:
    """mw-01: the overview as an agent sees it (queue first)."""
    page = run.staff("agent")
    page.goto("/admin/")
    page.get_by_role("heading", name="Wachtrij in mijn groepen").wait_for(timeout=15000)
    run.shot(
        page,
        "mw-01-overzicht",
        role=ROLES["agent"],
        visible=(
            'Het overzicht van de medewerker: vooraan "Wachtrij in mijn groepen" met per groep het '
            "aantal wachtende deelnemers, de langste wachttijd tegen het doel en het aantal "
            "aangemelde medewerkers; daarnaast Meldingen, daaronder Mijn rol en bereik en Uw "
            "groepen."
        ),
        how=Shot(mode="viewport"),
    )


@step("mw-02-wachtrij")
def mw_02(run: Run) -> None:
    """mw-02: Groepen → demo → Agenda → Wachtrij with today's booking, "Nog niet begonnen"."""
    need_queue(run)
    page = run.staff("agent")
    page.goto(f"/admin/groups/{GROUP}/queue")
    page.locator(QUEUE_ROWS).first.wait_for(timeout=15000)
    run.shot(
        page,
        "mw-02-wachtrij",
        role=ROLES["agent"],
        visible=(
            'Groepen → demo → Agenda → Wachtrij: "Wachtende afspraken" met de knoppen "Volgende '
            'oppakken" en Vernieuwen, en de afspraken die nu in de wachtrij staan (tijd, voornaam, '
            "afspraaknummer) met hun status: "
            + "; ".join(
                re.sub(r"\s+", " ", t).strip() for t in page.locator(QUEUE_ROWS).all_inner_texts()
            )
            + "."
        ),
        how=Shot(mode="viewport"),
    )


@step("mw-03-opgepakt")
def mw_03(run: Run) -> None:
    """mw-03: after "Volgende oppakken": the claimed booking."""
    need_queue(run)
    page = run.staff("agent")
    page.goto(f"/admin/groups/{GROUP}/queue")
    page.get_by_role("button", name="Volgende oppakken").click()
    page.get_by_role("heading", name=re.compile(r"^U heeft .* opgepakt")).first.wait_for(
        timeout=15000
    )
    run.state["claimed"] = page.url
    run.save()
    has_join = page.get_by_role("link", name=re.compile(r"Deelnemen aan het gesprek")).count() > 0
    run.shot(
        page,
        "mw-03-opgepakt",
        role=ROLES["agent"],
        visible=(
            'Na "Volgende oppakken": "U heeft … opgepakt" met tijd en deelnemer, de status van de '
            'deelnemer, en de knop "Vrijgeven".'
        )
        + (
            ""
            if has_join
            else (
                ' De knop "Deelnemen aan het gesprek" staat er niet: zonder Webex Meetings is er '
                "geen videolink."
            )
        ),
        how=Shot(mode="viewport"),
    )


@step("mw-04-vrijgegeven")
def mw_04(run: Run) -> None:
    """mw-04: after "Vrijgeven": the booking is back in the queue."""
    need_queue(run)
    page = run.staff("agent")
    if "/queue/" not in page.url:
        claimed = run.state.get("claimed")
        if not claimed:
            raise Skip("er is geen opgepakte afspraak (mw-03)")
        page.goto(claimed)
    page.get_by_role("button", name="Vrijgeven").first.click()
    page.wait_for_load_state("load")
    page.locator(QUEUE_ROWS).first.wait_for(timeout=15000)
    run.shot(
        page,
        "mw-04-vrijgegeven",
        role=ROLES["agent"],
        visible=(
            'Na "Vrijgeven": de melding dat de afspraak is vrijgegeven en weer in de wachtrij '
            'staat, en de afspraak weer in de lijst met "Nog niet begonnen".'
        ),
        how=Shot(mode="viewport"),
    )


@step("mw-05-mijn-rechten")
def mw_05(run: Run) -> None:
    """mw-05: Mijn rechten of the agent."""
    page = run.staff("agent")
    page.goto("/admin/rights")
    run.shot(
        page,
        "mw-05-mijn-rechten",
        role=ROLES["agent"],
        visible=(
            "Mijn rechten van de medewerker: de rol Medewerker voor groep demo en wat die rol mag."
        ),
        how=Shot(mode="full"),
    )


@step("mw-06-profielmenu")
def mw_06(run: Run) -> None:
    """mw-06: the open profile menu."""
    page = run.staff("agent")
    page.goto("/admin/")
    page.locator("details.admin-profile > summary").click()
    page.locator(".admin-profile__panel").wait_for(timeout=5000)
    run.shot(
        page,
        "mw-06-profielmenu",
        role=ROLES["agent"],
        visible=(
            "Het geopende profielmenu rechtsboven: naam en rol, link naar Mijn rechten, Weergave "
            "(thema), Taal, Meldingen (balk en pop-ups) en Uitloggen."
        ),
        how=Shot(mode="viewport"),
    )
    page.locator("details.admin-profile > summary").click()


@step("mw-07-mijn-sessies")
def mw_07(run: Run) -> None:
    """mw-07: Mijn rechten → Mijn sessies."""
    page = run.staff("agent")
    page.goto("/admin/rights/sessions")
    run.shot(
        page,
        "mw-07-mijn-sessies",
        role=ROLES["agent"],
        visible=(
            "Mijn rechten → Mijn sessies: de sessies van deze medewerker in het beheerportaal, met "
            "de huidige."
        ),
        how=Shot(mode="full"),
    )


# ---- Planner: overige schermen -----------------------------------------------------------


@step("pl-01-groep-overzicht")
def pl_01(run: Run) -> None:
    """pl-01: Groepen → demo → Overzicht."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}")
    settings = page.get_by_role("region", name="Instellingen")
    term = settings.locator("dt").filter(has_text=re.compile(r"^\s*Contactcenter\s*$"))
    row = [
        (term, "rij Contactcenter (term)"),
        (term.locator("xpath=following-sibling::dd[1]"), "rij Contactcenter (waarde)"),
    ]
    run.shot(
        page,
        "pl-01-groep-overzicht",
        role=ROLES["groupadmin"],
        visible=(
            'Groepen → demo → Overzicht: de kop van de groep met de tabbladen, "Klaar om te '
            'boeken" (wat nog te regelen is) en "Instellingen" van de groep (duur, aanlooptijd, '
            "vooruit boeken, hoe afspraken bij medewerkers komen: de eigen wachtrij, "
            "agenda-uitnodiging, telefoonnummer, videogesprek, direct gesprek)."
        ),
        how=Shot(mode="top", until=settings, cover=row),
        notes=[
            "Daaronder volgen Bezetting per week, Komende afwijkende bezetting en Zo zien "
            "deelnemers het (weggesneden)."
        ],
    )


@step("pl-02-bezetting")
def pl_02(run: Run) -> None:
    """pl-02: Planning → Bezetting: the week with time blocks."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/staffing")
    week = page.get_by_role("region", name="Bezetting per week")
    run.shot(
        page,
        "pl-02-bezetting",
        role=ROLES["groupadmin"],
        visible=(
            'Planning → Bezetting: "Bezetting per week", per dag de tijdblokken (09:00–12:00 en '
            "13:00–17:00, 2 medewerkers) met Wijzigen, Splitsen en Verwijderen."
        ),
        how=Shot(mode="top", until=week),
    )


@step("pl-03-tijdblok-toevoegen")
def pl_03(run: Run) -> None:
    """pl-03: the form "Tijdblok toevoegen", filled in before saving (not saved)."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/staffing")
    form = page.get_by_role("form", name="Tijdblok toevoegen")
    form.get_by_label("Dag").select_option(label="zaterdag")
    form.get_by_label(re.compile(r"^Van")).fill("10:00")
    form.get_by_label(re.compile(r"^Tot")).fill("13:00")
    form.get_by_label("Aantal medewerkers").fill("2")
    form.get_by_label("Aanbod").select_option(label="Afspraken")
    run.shot(
        page,
        "pl-03-tijdblok-toevoegen",
        role=ROLES["groupadmin"],
        visible=(
            'Het formulier "Tijdblok toevoegen" ingevuld vóór opslaan: zaterdag 10:00–13:00, 2 '
            "medewerkers, aanbod Afspraken, label en geldigheid leeg."
        ),
        how=Shot(mode="region", target=form, margin=16, full_width=False),
        notes=[
            "Niet opgeslagen: de weekbezetting is ongewijzigd. Uitsnede: alleen het formulier (het "
            "staat onder de weekbezetting van pl-02)."
        ],
    )


@step("pl-04-splitsen")
def pl_04(run: Run) -> None:
    """pl-04: the form "Splitsen" of a time block (not saved)."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/staffing")
    page.get_by_role("link", name="Tijdblok maandag 13:00–17:00 splitsen").click()
    form = page.get_by_role("form", name="Tijdblok splitsen")
    form.wait_for(timeout=15000)
    form.get_by_label(re.compile(r"^Splitsen om")).fill("16:00")
    form.get_by_label("Aantal medewerkers").fill("0")
    form.get_by_label(re.compile(r"^Label")).fill("Teamoverleg")
    run.shot(
        page,
        "pl-04-splitsen",
        role=ROLES["groupadmin"],
        visible=(
            'Het formulier "Tijdblok splitsen" voor maandag 13:00–17:00 (2 medewerkers), ingevuld: '
            "splitsen om 16:00, tweede deel 0 medewerkers met het label Teamoverleg."
        ),
        how=Shot(mode="full"),
        notes=[
            "Niet opgeslagen. Voorbeeld Teamoverleg in plaats van lunch: de demobezetting heeft de "
            "lunch al als gat tussen 12:00 en 13:00."
        ],
    )


@step("pl-06-feestdagen")
def pl_06(run: Run) -> None:
    """pl-06: Planning → Feestdagen."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/holidays")
    run.shot(
        page,
        "pl-06-feestdagen",
        role=ROLES["groupadmin"],
        visible="Planning → Feestdagen van de groep.",
        how=Shot(mode="full"),
    )


@step("pl-14-noodsluiting")
def pl_14(run: Run) -> None:
    """pl-14: the group's emergency closure form, not carried out."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/closure")
    form = page.get_by_role("form", name="Team noodsluiten")
    run.shot(
        page,
        "pl-14-noodsluiting",
        role=ROLES["groupadmin"],
        visible=(
            'Noodsluiting van de groep: het formulier "Team noodsluiten" (begin, einde, '
            "toelichting voor deelnemers in het Nederlands en Engels, kop) met het aantal geplande "
            "afspraken; niet uitgevoerd."
        ),
        how=Shot(mode="top", until=form),
        notes=["Niet uitgevoerd."],
    )


@step("pl-15-gebruik")
def pl_15(run: Run) -> None:
    """pl-15: Overzicht → Gebruik (the group's report)."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/usage")
    second = page.get_by_role("region", name="Boekingen gemaakt per dag").first
    run.shot(
        page,
        "pl-15-gebruik",
        role=ROLES["groupadmin"],
        visible=(
            "Groepen → demo → Overzicht → Gebruik: het gebruiksrapport van de groep met de periode "
            '(Van, Tot, Tonen), de regel dat aantallen onder 5 verborgen blijven ("< 5"), en het '
            'eerste rapport: Boekingen gemaakt per dag, met vandaag "voorlopig".'
        ),
        how=Shot(mode="top", until=second) if second.count() else Shot(mode="top"),
        notes=[
            "Daaronder volgen nog zes rapporten (hoe boekingen eindigden, vooruit boeken, "
            "annuleringen, verplaatsingen, bevestiging per e-mail, noodsluitingen), weggesneden."
        ],
    )


@step("pl-16-berichtteksten")
def pl_16(run: Run) -> None:
    """pl-16: Berichten → Berichtteksten."""
    page = run.staff("groupadmin")
    page.goto(f"/admin/groups/{GROUP}/messages")
    run.shot(
        page,
        "pl-16-berichtteksten",
        role=ROLES["groupadmin"],
        visible=(
            "Berichten → Berichtteksten van de groep (Nederlands): de teksten van de berichten aan "
            "deelnemers, per bericht aan te passen."
        ),
        how=Shot(mode="top"),
    )


# ---- Beheerder ---------------------------------------------------------------------------


def admin_page(run: Run, user: str, path: str) -> Page:
    """Open ``path`` as ``user``."""
    page = run.staff(user)
    page.goto(path)
    page.wait_for_load_state("load")
    return page


@step("bh-01-overzicht")
def bh_01(run: Run) -> None:
    """bh-01: Overzicht as super."""
    page = admin_page(run, "super", "/admin/")
    status = page.get_by_role("region", name="Systeemstatus")
    run.shot(
        page,
        "bh-01-overzicht",
        role=ROLES["super"],
        visible=(
            "Het overzicht van de applicatiebeheerder: widgets Meldingen, Wachtrij in mijn "
            "groepen, Afspraken vandaag per groep, Recente configuratiewijzigingen, Lopende taken "
            "en Systeemstatus."
        ),
        how=Shot(mode="top", until=status),
        notes=[
            "Op deze installatie loopt in de widget Systeemstatus de regel Proxy (rondgang naar "
            "het publieke adres mislukt, 127.0.0.1) rechts buiten de kaart en buiten het venster "
            "van 1280 px; zo staat het ook op het scherm."
        ],
    )


@step("bh-02-organisatie")
def bh_02(run: Run) -> None:
    """bh-02: Organisaties → demo → Overzicht."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}")
    run.shot(
        page,
        "bh-02-organisatie",
        role=ROLES["orgadmin"],
        visible=(
            "Organisaties → Demo-acme → Overzicht: de kop van de organisatie met tabbladen en het "
            "overzicht."
        ),
        how=Shot(mode="viewport"),
    )


@step("bh-03-org-instellingen")
def bh_03(run: Run) -> None:
    """bh-03: Overzicht → Instellingen of the organisation."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}/settings")
    run.shot(
        page,
        "bh-03-org-instellingen",
        role=ROLES["orgadmin"],
        visible=(
            "Overzicht → Instellingen van de organisatie: Bewaartermijnen, Weergave (tijdnotatie, "
            "tijdzone, de voettekstinstelling op Uit), Medewerkers, Planner, Boekingsformulier en "
            "Afspraken bevestigen, met bij elke waarde de bron."
        ),
        how=Shot(mode="full"),
    )


@step("bh-04-teksten")
def bh_04(run: Run) -> None:
    """bh-04: Deelnemers → Teksten of the organisation."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}/texts")
    run.shot(
        page,
        "bh-04-teksten",
        role=ROLES["orgadmin"],
        visible=(
            "Deelnemers → Teksten van de organisatie (Nederlands): de teksten op de "
            "deelnemerspagina's, aan te passen per organisatie."
        ),
        how=Shot(mode="top"),
    )


@step("bh-05-huisstijl")
def bh_05(run: Run) -> None:
    """bh-05: Deelnemers (branding) of the organisation, as super."""
    page = admin_page(run, "super", f"/admin/organisations/{ORG}/branding")
    run.shot(
        page,
        "bh-05-huisstijl",
        role=ROLES["super"],
        visible=(
            "Organisaties → Demo-acme → Deelnemers → Huisstijl: het voorbeeld van een kaart zoals "
            "deelnemers hem zien, en het formulier met naam in de kop, kop, accentkleur, kleuren, "
            "tekstgrootte, regelafstand, afronding, schaduw en Opslaan."
        ),
        how=Shot(mode="top", until=page.get_by_role("button", name="Opslaan").first, margin=32),
        notes=["Daaronder volgen Logo, Lettertypen en Themabestand (uploads), weggesneden."],
    )


@step("bh-06-kanalen")
def bh_06(run: Run) -> None:
    """bh-06: Deelnemers → Kanalen, as super."""
    page = admin_page(run, "super", f"/admin/organisations/{ORG}/channels")
    run.shot(
        page,
        "bh-06-kanalen",
        role=ROLES["super"],
        visible=(
            "Deelnemers → Kanalen van de organisatie: e-mail (en sms) voor berichten aan "
            "deelnemers."
        ),
        how=Shot(mode="top"),
    )


@step("bh-07-groepen")
def bh_07(run: Run) -> None:
    """bh-07: Groepen: the list."""
    page = admin_page(run, "orgadmin", "/admin/groups")
    run.shot(
        page,
        "bh-07-groepen",
        role=ROLES["orgadmin"],
        visible="Groepen: de lijst met de groep Demo: videogesprek van Demo-acme.",
        how=Shot(mode="viewport"),
    )


@step("bh-08-groep-nieuw")
def bh_08(run: Run) -> None:
    """bh-08: Nieuwe groep: the form, not saved."""
    page = admin_page(run, "orgadmin", "/admin/groups/new")
    run.shot(
        page,
        "bh-08-groep-nieuw",
        role=ROLES["orgadmin"],
        visible="Nieuwe groep: het lege formulier, niet opgeslagen.",
        how=Shot(mode="full"),
        notes=["Niet opgeslagen."],
    )


@step("bh-09-groepsinstellingen")
def bh_09(run: Run) -> None:
    """bh-09: Groepen → demo → Instellingen, with the own queue chosen."""
    page = admin_page(run, "orgadmin", f"/admin/groups/{GROUP}/settings")
    until = page.locator("details").filter(has_text=re.compile(r"Medewerkers mogen zelf"))
    fallback = page.get_by_label("Terugval als het contactcenter uitvalt").locator(
        "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' field ')][1]"
    )
    run.shot(
        page,
        "bh-09-groepsinstellingen",
        role=ROLES["orgadmin"],
        visible=(
            'Groepen → demo → Instellingen: de algemene instellingen van de groep, met bij "Hoe '
            'afspraken bij medewerkers komen" de eigen wachtrij gekozen en de velden van de eigen '
            "wachtrij."
        ),
        how=Shot(
            mode="top",
            until=until,
            margin=24,
            cover=[
                (fallback, "veld Terugval als het contactcenter uitvalt (label, keuze, uitleg)")
            ],
        ),
        notes=[
            "Bijgesneden onder de laatste velden van de eigen wachtrij en het telefoonnummer; "
            "daaronder volgen de koppelingen (video en contactcenter), de koppeling met de "
            "identiteitsprovider, het boekingsformulier en archiveren."
        ],
    )


@step("bh-10-boekingsformulier")
def bh_10(run: Run) -> None:
    """bh-10: Instellingen → Boekingsformulier."""
    page = admin_page(run, "orgadmin", f"/admin/groups/{GROUP}/settings/form")
    run.shot(
        page,
        "bh-10-boekingsformulier",
        role=ROLES["orgadmin"],
        visible=(
            "Instellingen → Boekingsformulier: de eigen vragen van de groep (met de uitgezette "
            "onderwerpvraag) en de soorten velden om toe te voegen."
        ),
        how=Shot(mode="top"),
    )


@step("bh-11-herinneringen")
def bh_11(run: Run) -> None:
    """bh-11: Berichten (reminders)."""
    page = admin_page(run, "orgadmin", f"/admin/groups/{GROUP}/reminders")
    run.shot(
        page,
        "bh-11-herinneringen",
        role=ROLES["orgadmin"],
        visible=(
            "Berichten van de groep: de herinneringen (e-mail 24 uur vooraf) en een herinnering "
            "toevoegen."
        ),
        how=Shot(mode="full"),
    )


@step("bh-12-medewerkers")
def bh_12(run: Run) -> None:
    """bh-12: Medewerkers: the list."""
    page = admin_page(run, "orgadmin", "/admin/staff")
    run.shot(
        page,
        "bh-12-medewerkers",
        role=ROLES["orgadmin"],
        visible=(
            'Medewerkers: de knop Medewerker uitnodigen, de lijst "Uitgenodigd, nog niet ingelogd" '
            "(twee ontwikkelaccounts), de filters en de tabel Medewerkers met naam, rollen, "
            "organisatie, groepen, inlogdienst en laatste login, actief en afgemeld."
        ),
        how=Shot(mode="top", until=page.get_by_role("table", name="Medewerkers")),
    )


@step("bh-13-medewerker")
def bh_13(run: Run) -> None:
    """bh-13: the page of one staff member (the agent)."""
    page = admin_page(run, "orgadmin", "/admin/staff")
    page.get_by_role("link", name=re.compile(r"Mo Medewerker")).first.click()
    page.wait_for_load_state("load")
    run.shot(
        page,
        "bh-13-medewerker",
        role=ROLES["orgadmin"],
        visible=(
            "De pagina van één medewerker (Mo Medewerker): gegevens uit de identiteitsprovider, "
            "rollen en groepen, en wat de beheerder hier kan doen."
        ),
        how=Shot(mode="full"),
    )


@step("bh-14-rechtenmatrix")
def bh_14(run: Run) -> None:
    """bh-14: Rechtenmatrix."""
    page = admin_page(run, "orgadmin", "/admin/matrix")
    run.shot(
        page,
        "bh-14-rechtenmatrix",
        role=ROLES["orgadmin"],
        visible=(
            "De rechtenmatrix: per recht welke rol het heeft en op welk niveau (het begin van de "
            "tabel)."
        ),
        how=Shot(mode="viewport"),
    )


@step("bh-15-eigen-rol")
def bh_15(run: Run) -> None:
    """bh-15: Rechtenmatrix → Rollen aanpassen → Nieuwe rol (super), not saved."""
    page = admin_page(run, "super", "/admin/roles/new")
    run.shot(
        page,
        "bh-15-eigen-rol",
        role=ROLES["super"],
        visible=(
            'Rechtenmatrix → Rollen aanpassen → Nieuwe rol: "Nieuwe rol maken" (kopie van '
            "Organisatiebeheerder) met de namen in het Nederlands en Engels, de waarde in de "
            "identity provider, de uitleg over het toekennen, en het begin van de lijst Rechten; "
            "niet opgeslagen."
        ),
        how=Shot(mode="top", until=page.get_by_role("button", name="Rol maken")),
        notes=[
            "Niet opgeslagen.",
            "Weergavefout op het scherm: de rechten staan in één rij naast elkaar die ver buiten "
            "het venster doorloopt (de pagina wordt zo'n 7300 px breed); de uitleg links ernaast "
            "breekt daardoor na elk woord af en de meeste vinkjes vallen rechts buiten beeld. De "
            "opname laat zien wat er bij 1280 px in beeld staat.",
        ],
    )


@step("bh-16-mijn-rechten")
def bh_16(run: Run) -> None:
    """bh-16: Mijn rechten of the organisation admin."""
    page = admin_page(run, "orgadmin", "/admin/rights")
    run.shot(
        page,
        "bh-16-mijn-rechten",
        role=ROLES["orgadmin"],
        visible=(
            "Mijn rechten van de organisatiebeheerder: de rol Organisatiebeheerder voor "
            "organisatie demo en de rechten die daarbij horen."
        ),
        how=Shot(mode="top"),
    )


@step("bh-17-auditlog")
def bh_17(run: Run) -> None:
    """bh-17: Auditlog."""
    page = admin_page(run, "orgadmin", "/admin/audit")
    run.shot(
        page,
        "bh-17-auditlog",
        role=ROLES["orgadmin"],
        visible=(
            "Het auditlog van de organisatie: filters en de gebeurtenissen van deze opname "
            "(logins, wijzigingen, berichten)."
        ),
        how=Shot(mode="viewport"),
    )


@step("bh-18-bewaartermijnen")
def bh_18(run: Run) -> None:
    """bh-18: Bewaartermijnen."""
    page = admin_page(run, "orgadmin", "/admin/retention")
    run.shot(
        page,
        "bh-18-bewaartermijnen",
        role=ROLES["orgadmin"],
        visible="Bewaartermijnen: wanneer welke gegevens worden verwijderd.",
        how=Shot(mode="full"),
    )


@step("bh-19-insluiten")
def bh_19(run: Run) -> None:
    """bh-19: Overzicht → Insluiten of the organisation."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}/embedding")
    run.shot(
        page,
        "bh-19-insluiten",
        role=ROLES["orgadmin"],
        visible=(
            "Overzicht → Insluiten (Inbedding) van de organisatie: de toegestane websites voor de "
            "widget en de code om hem in te sluiten."
        ),
        how=Shot(mode="full"),
    )


@step("bh-20-feestdagen-org")
def bh_20(run: Run) -> None:
    """bh-20: Feestdagen of the organisation."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}/holidays")
    run.shot(
        page,
        "bh-20-feestdagen-org",
        role=ROLES["orgadmin"],
        visible="Feestdagen van de organisatie: welke feestdagkalender de organisatie volgt.",
        how=Shot(mode="full"),
    )


@step("bh-21-noodsluiting-org")
def bh_21(run: Run) -> None:
    """bh-21: Noodsluiting of the organisation: the form, not carried out."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}/closure")
    run.shot(
        page,
        "bh-21-noodsluiting-org",
        role=ROLES["orgadmin"],
        visible=(
            "Noodsluiting van de organisatie: het formulier om alle groepen tegelijk te sluiten; "
            "niet uitgevoerd."
        ),
        how=Shot(mode="full"),
        notes=["Niet uitgevoerd."],
    )


@step("bh-22-integraties")
def bh_22(run: Run) -> None:
    """bh-22: Integraties (super): only the Webex Meetings part."""
    page = admin_page(run, "super", "/admin/integrations")
    section = page.get_by_role("region", name="Webex Service App")
    if not section.count():
        raise Skip("geen onderdeel Webex Service App op de pagina Koppelingen")
    keep = section.locator("dt").filter(has_text="Verversingstoken geldig tot").locator("xpath=..")
    run.shot(
        page,
        "bh-22-integraties",
        role=ROLES["super"],
        visible=(
            'Koppelingen (in het menu Platform), alleen het onderdeel "Webex Service App": de '
            "koppeling met Webex voor de videogesprekken (Service App: Niet gekoppeld; toegangs- "
            "en verversingstoken: Nog niet gebruikt)."
        ),
        how=Shot(mode="region", target=section, bottom=keep, margin=10, full_width=False),
        notes=[
            "Uitsnede: alleen de kaart Webex Service App. De laatste regel van die kaart (de "
            "koppeling met het contactcenter) en de rest van de pagina vallen buiten de uitsnede. "
            'Een apart onderdeel "Webex Meetings" heeft deze pagina niet; de Service App is de '
            "Webex-koppeling voor de videogesprekken."
        ],
    )


@step("bh-23-systeemstatus")
def bh_23(run: Run) -> None:
    """bh-23: Geavanceerde instellingen (super): system status."""
    page = admin_page(run, "super", "/admin/settings")
    run.shot(
        page,
        "bh-23-systeemstatus",
        role=ROLES["super"],
        visible=(
            "Geavanceerde instellingen → Systeemstatus: de omgeving, logbestemmingen, "
            "medewerkerbeheer en identity providers van organisaties (het begin van de pagina)."
        ),
        how=Shot(mode="top"),
        notes=[
            "Weergavefout op het scherm: bij Omgeving staat de ruwe programmawaarde "
            "\"EnvironmentNotice(label_key='admin.environment.dev', simulator=False)\" in plaats "
            "van een tekst, en die loopt buiten het vak.",
            "Verderop op de pagina (niet in de uitsnede): back-ups, gebruiksrapportage, adres en "
            "proxy met de mislukte rondgang naar 127.0.0.1, het certificaat (geen https) en de "
            "lijst variabelen.",
        ],
    )


@step("bh-24-platforminstellingen")
def bh_24(run: Run) -> None:
    """bh-24: Geavanceerde instellingen → Instellingen (super)."""
    page = admin_page(run, "super", "/admin/settings/values")
    run.shot(
        page,
        "bh-24-platforminstellingen",
        role=ROLES["super"],
        visible=(
            "Geavanceerde instellingen → Instellingen: de platforminstellingen per onderwerp, met "
            "waarde, bron en grenzen."
        ),
        how=Shot(mode="top"),
    )


@step("bh-25-updates")
def bh_25(run: Run) -> None:
    """bh-25: Updates (super)."""
    page = admin_page(run, "super", "/admin/updates")
    version = page.get_by_role("region", name="Versie")
    fields = [
        (
            version.locator("dt")
            .filter(has_text=re.compile(rf"^\s*{label}\s*$"))
            .locator("xpath=following-sibling::dd[1]"),
            f"waarde van {label}",
        )
        for label in ("Commit", "Nieuwste versie")
    ]
    run.shot(
        page,
        "bh-25-updates",
        role=ROLES["super"],
        visible=(
            "Updates: de melding dat deze installatie niet naar nieuwe versies zoekt, het blok "
            "Versie (draait nu, commit, kanaal, lijn, updatevenster, ondersteuning, nieuwste "
            "versie, laatst gecontroleerd) en de Geschiedenis (nog geen updates)."
        ),
        how=Shot(mode="full", cover_versions=fields),
    )


@step("bh-26-onderhoud")
def bh_26(run: Run) -> None:
    """bh-26: Onderhoud (super)."""
    page = admin_page(run, "super", "/admin/maintenance")
    run.shot(
        page,
        "bh-26-onderhoud",
        role=ROLES["super"],
        visible="Onderhoud: de onderhoudsmodus aan- of uitzetten.",
        how=Shot(mode="full"),
    )


@step("bh-27-logbestemmingen")
def bh_27(run: Run) -> None:
    """bh-27: Logbestemmingen (super)."""
    page = admin_page(run, "super", "/admin/logging")
    run.shot(
        page,
        "bh-27-logbestemmingen",
        role=ROLES["super"],
        visible=(
            "Logbestemmingen: waar het log heen gaat (hier nog geen bestemming) en een bestemming "
            "toevoegen."
        ),
        how=Shot(mode="full"),
    )


@step("bh-28-idp-koppeling")
def bh_28(run: Run) -> None:
    """bh-28: Koppeling identiteitsprovider."""
    page = admin_page(run, "orgadmin", "/admin/idp-mapping")
    run.shot(
        page,
        "bh-28-idp-koppeling",
        role=ROLES["orgadmin"],
        visible=(
            "Koppeling identiteitsprovider: welke waarden de identiteitsprovider moet meesturen, "
            "de instellingen (provider, adres, medewerkers beheren, bron van de rechten, claim, "
            "voorvoegsel) en het begin van de waarden voor Demo-acme."
        ),
        how=Shot(mode="top"),
        notes=[
            "De claimnaam en het voorvoegsel bevatten de productnaam (afgedekt); het adres "
            "(issuer) eindigt op de realmnaam, ook de productnaam (afgedekt).",
            "Weergavefout op het scherm: het adres (issuer) breekt niet af en loopt over de kaart "
            "Medewerkers beheren ernaast; de balk over de productnaam in dat adres dekt daardoor "
            "ook een stukje van de tekst van die kaart af.",
        ],
    )


@step("bh-29-inloggen-org")
def bh_29(run: Run) -> None:
    """bh-29: Overzicht → Inloggen of the organisation."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}/login")
    run.shot(
        page,
        "bh-29-inloggen-org",
        role=ROLES["orgadmin"],
        visible=(
            "Overzicht → Inloggen van de organisatie: met welke identiteitsprovider de medewerkers "
            "inloggen."
        ),
        how=Shot(mode="full"),
    )


@step("bh-30-handleiding")
def bh_30(run: Run) -> None:
    """bh-30: Handleiding in the portal (built-in help)."""
    page = admin_page(run, "orgadmin", "/admin/help")
    run.shot(
        page,
        "bh-30-handleiding",
        role=ROLES["orgadmin"],
        visible="De ingebouwde handleiding in het portaal: de onderwerpen, één per scherm.",
        how=Shot(mode="viewport"),
    )


@step("bh-31-contactalternatieven")
def bh_31(run: Run) -> None:
    """bh-31: Contact of the organisation."""
    page = admin_page(run, "orgadmin", f"/admin/organisations/{ORG}/contacts")
    run.shot(
        page,
        "bh-31-contactalternatieven",
        role=ROLES["orgadmin"],
        visible=(
            "Contact van de organisatie: de contactalternatieven die deelnemers zien bij een "
            "sluiting (telefoon, website, adres)."
        ),
        how=Shot(mode="top"),
    )


@step("bh-32-compliance-overzicht")
def bh_32(run: Run) -> None:
    """bh-32: Overzicht as the auditor sees it."""
    page = admin_page(run, "auditor", "/admin/")
    run.shot(
        page,
        "bh-32-compliance-overzicht",
        role=ROLES["auditor"],
        visible=(
            "Het overzicht van compliance en privacy (alleen lezen): meldingen, "
            "configuratiewijzigingen en Mijn rol en bereik, zonder persoonsgegevens."
        ),
        how=Shot(mode="viewport"),
    )


# ---- Webex Meetings (a test environment with Webex only) --------------------------------


def _webex(run: Run, cid: str) -> None:
    """Skip an id that needs Webex Meetings, which this installation does not have."""
    raise Skip(
        "vraagt Webex Meetings; deze installatie heeft geen Webex (in een testomgeving met Webex "
        "opnemen)"
    )


for _cid in WEBEX_IDS:
    STEPS.append((_cid, lambda run, cid=_cid: _webex(run, cid)))


# --------------------------------------------------------------------------------------------
# The log
# --------------------------------------------------------------------------------------------


def list_entries() -> list[tuple[str, str]]:
    """Return (id, description) of every row of opnamelijst.md, in its order."""
    rows = []
    for line in LIST_FILE.read_text().splitlines():
        match = re.match(r"^\|\s*((?:bb|mw|pl|bh)-\d\d-[a-z0-9-]+)\s*\|\s*(.+?)\s*\|\s*$", line)
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def portal_version(run: Run) -> dict[str, str]:
    """Read what the portal says about its version (Updates and the overview's system status)."""
    page = run.staff("super")
    page.goto("/admin/updates")
    facts = page.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('dt')]"
        ".map(d => [d.innerText.trim(), (d.nextElementSibling||{}).innerText||'']))"
    )
    page.goto("/admin/")
    status = page.evaluate(
        "() => { const li = [...document.querySelectorAll('li')]"
        ".find(l => /^\\s*Versie/.test(l.innerText)); "
        "return li ? li.innerText.replace(/\\s+/g,' ').trim() : ''; }"
    )
    return {
        "draait_nu": facts.get("Draait nu", "?").strip(),
        "commit": facts.get("Commit", "?").strip(),
        "kanaal": facts.get("Kanaal", "?").strip(),
        "overzicht": status,
    }


def write_log(run: Run) -> None:
    """Write bewijs/bewijslog.md from the records of every invocation so far."""
    stack = run.stack
    version = run.state.get("version", {})
    started = run.state.get("started", "?")
    lines = [
        "# Bewijslog schermafdrukken",
        "",
        "Elke afbeelding in `img/` komt uit de draaiende software hieronder, via echte "
        "gebruikerspaden. Bewerkingen: alleen bijsnijden en een effen lichtgrijze balk (RGB 208, "
        "208, 208) over de productnaam of het beeldmerk (afdekregel 1), over een "
        "contactcenterwoord, -rij, -kolom of -blok (afdekregel 2) of over een versie (afdekregel "
        "3), getekend met Pillow op het vak dat de pagina zelf voor dat stuk tekst of dat element "
        f"opgeeft (plus {PAD} px rand). Gemaakt met `scripts/capture.py`.",
        "",
        "## Software en opname",
        "",
        f"- Commit: `{stack.commit}` (gebouwd door de repo-skill verify-openplanner, run "
        f"`{stack.run_id}`, lokale wijzigingen: "
        f"{dict(yes='ja', no='nee').get(stack.local_changes, stack.local_changes)}); image "
        f"`{stack.image}`.",
        f'- Wat het portaal toont, **Updates**: Draait nu "{version.get("draait_nu", "?")}"; '
        f'Commit "{version.get("commit", "?")}"; Kanaal "{version.get("kanaal", "?")}". '
        f'**Overzicht → Systeemstatus**: "{version.get("overzicht", "?")}". Het portaal toont dus '
        "geen versienummer en geen commit; de commit hierboven komt uit het bouwverslag van de "
        "run.",
        f"- Installatie `{stack.installation}` op {stack.web} (beheerportaal {stack.web}/admin/, "
        f"identiteitsprovider {stack.keycloak}, Mailpit {stack.mail}); gecontroleerd via "
        "`/api/v1/instance` vóór elke run.",
        f"- Opname gestart {started}; laatst bijgewerkt {stamp()}. Tijden in Europe/Amsterdam.",
        "- Venster 1280 × 800, schaal 2 (afbeeldingen 2560 px breed); de brede overzichten "
        f"{', '.join(sorted(WIDE_IDS))} in {WIDE['width']} × {WIDE['height']} (afbeeldingen "
        f"{WIDE['width'] * SCALE} px breed), omdat hun tabellen bij 1280 px de rechterkolom "
        "verliezen; mobiel 390 × 844, schaal 2. E-mail: het HTML-deel uit Mailpit, weergegeven op "
        f"{EMAIL_WIDTH} px breed, alleen het bericht. Licht thema, taal nl-NL, beweging beperkt.",
        "- Bij een opname van een hele pagina of een bovenstuk is het venster even zo hoog gemaakt "
        "als de pagina (de breedte blijft gelijk), zodat de zijbalk meeloopt; daarna weer de "
        "vorige maat.",
        '- In het beheerportaal is de omgevingsbalk "Ontwikkelomgeving – Niet voor echte afspraken '
        'van deelnemers." bovenaan weggesneden (bijsnijden); hij staat alleen op een ontwikkel- of '
        "testinstallatie.",
        "- Afdekregel 1 (opdracht): de productnaam (ook in kleine letters, in adressen en "
        "e-mailadressen) en het beeldmerk.",
        "- Afdekregel 2 (aanvulling van de coördinator, "
        + str(run.state.get("cc_rule_since", "?"))
        + "; de producteigenaar wil nog geen "
        "contactcenterkoppeling noemen): de woorden Contactcenter, contactcenter, "
        "Contactcentertaak, contactcentertaak, Webex Contact Center, WxCC en Agent Desktop, ook in "
        "samenstellingen (zoals WxCC-flow). Een hele kolom, rij of blok dat alleen zulke inhoud "
        "heeft, is weggesneden als het aan de rand staat en anders als geheel afgedekt; dat staat "
        "per opname als contactcenterblok of contactcenterkolom vermeld.",
        "- Afdekregel 3 (tweede aanvulling van de coördinator): geen leesbare versie. Afgedekt "
        'worden softwareversies (zoals 0.5.1, v0.5.1, 0.5.1-rc.2 en het label dev), "Versie '
        'onbekend", "Geen release(versie)", commit-hashes (7 tot 40 hexadecimale tekens), '
        "image-id's en digests (sha256:…) en de waarden van versievelden (Updates: commit en "
        "nieuwste versie). Datums en tijden blijven staan. Een scan van alle beheerschermen vond "
        "versies alleen op bh-01, bh-23, bh-25 en bh-32; die zijn daarna opnieuw opgenomen. De "
        "deelnemerspagina's en e-mails bevatten geen versie (gecontroleerd in de sjablonen).",
        '- Geen Webex Meetings en geen contactcenter op deze installatie: zie per opname "Zonder '
        'Webex".',
        "- Verzonnen deelnemers: Sanne Jansen, Ahmed el Amrani, Pieter de Bakker (Bakkerij De "
        "Korenaar), adressen op voorbeeld.nl; medewerkers zijn de ontwikkelgebruikers van de "
        "identiteitsprovider. Geen token, wachtwoord of e-mailtekst in dit log.",
        "",
        "## Voorbereiding en verhaal",
        "",
    ]
    prep = run.state.get("prep", {})
    order = ["1", "2", "2b", "3", "tekort"]
    for key in sorted(prep, key=lambda k: (order.index(k) if k in order else len(order), k)):
        lines.append(f"- **{key}** ({prep[key]['at']}): {prep[key]['text']}")
    lines.append("")
    if run.bookings:
        lines += [
            "Boekingen van het verhaal (alle via de pagina gemaakt en via de link in de e-mail "
            "bevestigd):",
            "",
        ]
        lines += [
            "| Sleutel | Wie | Dag | Tijd | Kenmerk in het adres `/b/m/…` | Stand aan het eind |",
            "|---|---|---|---|---|---|",
        ]
        status_nl = {
            "confirmed": "bevestigd",
            "moved": "verplaatst (geannuleerd, nieuwe afspraak)",
            "cancelled": "geannuleerd door de deelnemer",
            "waiting": "wacht op nieuwe tijd (planner vroeg erom)",
            "pending": "wacht op bevestiging",
        }
        for key, b in run.bookings.items():
            who = " ".join(PEOPLE.get(b.get("who", ""), ("?", "", ""))[:2])
            status = status_nl.get(b.get("status", ""), b.get("status", "?"))
            lines.append(
                f"| {key} | {who} | {b.get('day', '?')} | {b.get('time', '?')} | "
                f"{b.get('reference', '?')} | {status} |"
            )
        lines += [
            "",
            "Het portaal toont afspraken als OP-000001 tot en met OP-000007 in de volgorde van de "
            "tabel (de verplaatste afspraak kreeg een eigen nummer).",
        ]
        lines.append("")
    lines += ["## Opnamen", ""]
    titles = dict(list_entries())
    for cid, title in list_entries():
        record = run.records.get(cid)
        lines.append(f"### {cid}")
        lines.append("")
        lines.append(f"*Lijst:* {title}")
        lines.append("")
        if not record and cid in WEBEX_IDS:
            lines += [
                "- Status: **niet opgenomen**: vraagt Webex Meetings; deze installatie heeft geen "
                "Webex. Op te nemen in een testomgeving met Webex Meetings (`capture.py "
                '--with-webex`); tot dan het kader "Schermafdruk volgt".',
                "",
            ]
            continue
        if not record:
            lines += ["- Status: nog niet gedraaid.", ""]
            continue
        if record["status"] != "opgenomen":
            lines += [f"- Status: **niet opgenomen** ({record['tijd']}): {record['reden']}", ""]
            continue
        lines.append(f"- Rol: {record['rol']}")
        lines.append(f"- Adres: `{record['adres']}`")
        lines.append(f"- Tijd: {record['tijd']}")
        lines.append(f"- Zichtbaar: {record['zichtbaar']}")
        if record.get("kop"):
            lines.append(f"- Kop op de pagina: {', '.join(record['kop'])}")
        if record.get("meldingen"):
            lines.append(f"- Meldingen in beeld: {' | '.join(record['meldingen'])}")
        if record.get("zonder_webex"):
            lines.append(
                "- Zonder Webex: het scherm toont "
                f"{', '.join(chr(34) + p + chr(34) for p in record['zonder_webex'])}."
            )
        lines.append(
            f"- Bijsnijding: {'; '.join(record['bijsnijding'])}; afbeelding "
            f"{record['afmeting_px'][0]} × {record['afmeting_px'][1]} px."
        )
        if record["afdekkingen"]:
            lines.append(f"- Afdekkingen ({len(record['afdekkingen'])}):")
            for n, red in enumerate(record["afdekkingen"], 1):
                context = red["context"].replace("|", "/")
                context = re.sub(r"(?i)openplanner", "[productnaam]", context)
                lines.append(
                    f"  {n}. {red['wat']}: "
                    f'"{re.sub(r"(?i)openplanner", "[productnaam]", red["tekst"])}" in '
                    f'"{context}", vak {red["vak_px"]} (px op de afbeelding).'
                )
        else:
            lines.append(
                "- Afdekkingen: geen (geen productnaam, beeldmerk of contactcenterwoord in beeld)."
            )
        for note in record.get("notities", []):
            lines.append(f"- Notitie: {note}")
        since = run.state.get("cc_rule_since")
        if (
            since
            and record["tijd"] < since
            and not any(r["wat"].startswith("contactcenter") for r in record["afdekkingen"])
        ):
            lines.append(
                "- Afdekregel 2: deze opname is van vóór de aanvulling en niet opnieuw te maken "
                "(de toestand bestaat niet meer); gecontroleerd dat de deelnemerspagina's en de "
                "e-mails aan deelnemers geen van die woorden bevatten (sjablonen en teksten van de "
                "software), dus er was niets af te dekken."
            )
        lines.append("")
    extra = [cid for cid in run.records if cid not in titles]
    if extra:
        lines += ["## Overige", ""] + [f"- {cid}: {run.records[cid]}" for cid in extra] + [""]
    LOG_FILE.write_text("\n".join(lines))


# --------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------


def main() -> int:
    """Run the chosen steps of the story against the stack and rewrite the evidence log."""
    if ".claude/skills" in Path(__file__).resolve().as_posix():
        sys.exit(
            f"{Path(__file__).name}: copy the scripts to a workspace outside the repository first "
            "(see the role-manuals SKILL.md): manuals hold an organisation's own material."
        )
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="print the steps in story order")
    parser.add_argument("--only", help="comma-separated steps to run")
    parser.add_argument("--from", dest="start", help="run from this step onward")
    parser.add_argument(
        "--with-webex", action="store_true", help="also try the ids that need Webex Meetings"
    )
    parser.add_argument(
        "--no-wait", action="store_true", help="do not wait for the queue window; record why"
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument(
        "--installation",
        default=None,
        help="installation id that must answer (default: the INSTANCE_ID in the run's stack.env)",
    )
    parser.add_argument(
        "--day",
        help=(
            "agenda day YYYY-MM-DD for pl-07, pl-08 and the search of pl-13 (default: the story's "
            "weekdays)"
        ),
    )
    parser.add_argument(
        "--log-only",
        action="store_true",
        help="only rewrite bewijs/bewijslog.md from bewijs/opname.json",
    )
    args = parser.parse_args()

    names = [name for name, _ in STEPS]
    if args.list:
        print("\n".join(names))
        return 0
    chosen = names
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        unknown = [w for w in wanted if w not in names]
        if unknown:
            sys.exit(f"capture.py: unknown step(s) {', '.join(unknown)}; see --list")
        chosen = [n for n in names if n in wanted]
    elif args.start:
        if args.start not in names:
            sys.exit(f"capture.py: unknown step {args.start}; see --list")
        chosen = names[names.index(args.start) :]
    if not args.with_webex:
        chosen = [n for n in chosen if n not in WEBEX_IDS]

    stack = load_stack()
    if args.log_only:
        if not STATE_FILE.exists():
            sys.exit(f"capture.py: no {STATE_FILE}")
        write_log(
            Run(
                stack=stack,
                mail=Mail(stack.mail, stack.web),
                browser=None,
                state=json.loads(STATE_FILE.read_text()),
            )
        )
        print(f"capture.py: {LOG_FILE} rewritten from {STATE_FILE}")
        return 0
    expected = stack.installation if args.installation is None else args.installation
    assert_installation(stack, expected)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if state.get("installation") not in (None, stack.installation):
        sys.exit(
            f"capture.py: {STATE_FILE} belongs to installation {state['installation']}; "
            "move it away before a run on a new stack"
        )
    if args.day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.day):
        sys.exit("capture.py: --day takes YYYY-MM-DD")
    state["agenda_day"] = args.day or None
    state["installation"] = stack.installation
    state.setdefault("started", stamp())
    state.setdefault("cc_rule_since", stamp())
    print(
        f"capture.py: installation {stack.installation} (run {stack.run_id}, commit "
        f"{stack.commit[:12]}) at {stack.web}"
    )

    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed)
        run = Run(stack=stack, mail=Mail(stack.mail, stack.web), browser=browser, state=state)
        if "version" not in state or not args.only:
            state["version"] = portal_version(run)
            run.save()
        deferred: list[tuple[str, dt.datetime]] = []
        fns = dict(STEPS)
        for name in chosen:
            print(f"- {name}")
            try:
                with run.viewport_for(name):
                    fns[name](run)
            except NotYet as wait:
                print(f"  ..   uitgesteld: {wait.why}")
                deferred.append((name, wait.until))
            except Skip as reason:
                run.skipped(name, str(reason)) if not name.startswith("s-") else print(
                    f"  --   {name}: {reason}"
                )
            except (PlaywrightTimeout, RuntimeError) as error:
                failures += 1
                message = str(error).splitlines()[0][:300]
                print(f"  FAIL {name}: {message}")
                traceback.print_exc(limit=3)
                if not name.startswith("s-"):
                    run.skipped(name, f"mislukt: {_safe_url(message)}")
        for name, until in deferred:
            wait = (until - now_local()).total_seconds()
            if wait > 0 and args.no_wait:
                run.skipped(
                    name, f"niet gewacht op het venster van de wachtrij (vanaf {until:%H:%M})"
                )
                continue
            if wait > 0:
                print(
                    f"- {name}: wacht tot {until:%H:%M:%S} ({int(wait)} s) op het venster van de "
                    "wachtrij"
                )
                time.sleep(wait)
            print(f"- {name}")
            try:
                with run.viewport_for(name):
                    fns[name](run)
            except (Skip, NotYet) as reason:
                run.skipped(name, str(reason))
            except (PlaywrightTimeout, RuntimeError) as error:
                failures += 1
                run.skipped(name, f"mislukt: {_safe_url(str(error).splitlines()[0][:300])}")
        write_log(run)
        browser.close()
    done = sum(1 for r in state.get("records", {}).values() if r.get("status") == "opgenomen")
    print(f"capture.py: {done} opnamen in {IMG}; log {LOG_FILE}; {failures} mislukt in deze run")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
