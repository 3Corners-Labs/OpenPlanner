"""The interface texts that the skill scripts drive are the texts the app shows.

``.claude/skills/verify-openplanner/scripts/labels.json`` is the one home of the Dutch texts the
scripts of ``verify-openplanner`` and ``role-manuals`` wait for, fill in or cover. No CI job runs
those scripts, so this test holds each text against the catalogue: a pull request that renames a
text the scripts use fails here, and updates the handle in the same pull request.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from babel.dates import get_day_names

from app.i18n.po import load_catalogues

ROOT = Path(__file__).resolve().parents[1]
LABELS = json.loads(
    (ROOT / ".claude" / "skills" / "verify-openplanner" / "scripts" / "labels.json").read_text()
)
NL = load_catalogues()["nl"]
SEED = (ROOT / "app" / "adapters" / "db" / "seed.py").read_text()
REALM = json.loads((ROOT / "dev" / "keycloak" / "openplanner-realm.json").read_text())
NAMES = {f"{u.get('firstName', '')} {u.get('lastName', '')}".strip() for u in REALM["users"]}
"""The display names of the development users, as the portal shows them."""


def _entries() -> Iterator[tuple[str, dict[str, str]]]:
    """Yield every label as ``group.name`` with its entry."""
    for group, entries in LABELS.items():
        if isinstance(entries, dict):
            for name, entry in entries.items():
                yield f"{group}.{name}", entry


def _shown(key: str) -> str:
    """Return the catalogue text of ``key`` with the default agent term filled in (ADR 0077)."""
    agent, agents = NL["term.agent"], NL["term.agents"]
    text = re.sub(r"\s+", " ", NL[key]).strip()
    for placeholder, value in {
        "{agent}": agent,
        "{agents}": agents,
        "{Agent}": agent[:1].upper() + agent[1:],
        "{Agents}": agents[:1].upper() + agents[1:],
    }.items():
        text = text.replace(placeholder, value)
    return text


ENTRIES = list(_entries())


@pytest.mark.parametrize(("name", "entry"), ENTRIES, ids=[name for name, _ in ENTRIES])
def test_a_label_is_the_text_the_app_shows(name: str, entry: dict[str, str]) -> None:
    """Each label matches its catalogue text, or the seed, realm or locale it comes from."""
    source = entry.get("source")
    if source == "locale":
        assert entry["text"] in get_day_names("wide", locale="nl").values(), name
        return
    if source == "seed":
        assert entry["text"] in SEED, f"{name}: not in the demo seed"
        return
    if source == "realm":
        assert entry["text"] in NAMES, f"{name}: no development user has this name"
        return
    assert entry["key"] in NL, f"{name}: the catalogue has no key {entry['key']}"
    shown = _shown(entry["key"])
    if "template" in entry:
        assert shown == entry["template"], f"{name}: the catalogue says {shown!r}"
    elif "pattern" in entry:
        assert re.search(entry["pattern"], shown), f"{name}: the catalogue says {shown!r}"
    else:
        text = entry["text"]
        matches = {
            "exact": shown == text,
            "prefix": shown.startswith(text),
            "contains": text in shown,
        }
        assert matches[entry.get("match", "exact")], f"{name}: the catalogue says {shown!r}"


def test_every_label_says_where_its_text_comes_from() -> None:
    """A label names a catalogue key or a known source, and carries something to match."""
    for name, entry in ENTRIES:
        assert ("key" in entry) != ("source" in entry), name
        assert {"text", "pattern", "template"} & entry.keys(), name
        assert entry.get("source") in (None, "seed", "realm", "locale"), name
        assert entry.get("match", "exact") in ("exact", "prefix", "contains"), name
