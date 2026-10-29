"""Prepared closure scenarios: reusable closure content (ADR 0037 §4).

A ``ClosureScenario`` is what an emergency closure says, prepared before the emergency: a
name for admins, the notice and optional heading per locale, optionally a contact block and an
image. It belongs to an organisation (``group_id`` ``None``) or to one group of it.

    organisation scenario   organisation.closure; every group of the organisation uses it
    group scenario          closure.manage on that group; only that group uses it

Starting a closure from a scenario copies its content into the closure
(``app.domain.scenario_service.usable_scenario`` says which scenarios a group may start
from): editing the scenario later never changes a running closure. A scenario without a
contact block takes the block that applies to the group when the closure starts. Scenario
images are shown to admins only, so a prepared "strike" does not leak before it is used.
Texts follow the limits of the closure itself (ADR 0037 §6).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.domain.closure import NOTICE_LOCALES, clean_headings, clean_notice, notice_field
from app.domain.contacts import ContactBlock
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import PageImage

MAX_NAME_LENGTH = 80
"""Longest scenario name; a label in a list, like a contact option's label (ADR 0037 §6)."""

_LINE_BREAKS_OR_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ScenarioOwner:
    """Whose scenarios: an organisation's or a group's, by slug. Exactly one is given.

    Raises:
        ValueError: When neither or both are given (a programming error).
    """

    organisation: str | None = None
    group: str | None = None

    def __post_init__(self) -> None:
        """Check that exactly one owner is named."""
        if (self.organisation is None) == (self.group is None):
            raise ValueError("a scenario owner is exactly one organisation or group")


@dataclass(frozen=True)
class ClosureScenario:
    """Prepared content for an emergency closure.

    Attributes:
        id: Database id; ``None`` before it is stored.
        organisation_id: The organisation the scenario belongs to.
        group_id: The group of a group scenario; ``None`` for an organisation scenario.
        name: What admins call it ("Storing", "Staking"); never shown to citizens.
        notices: The notice per locale (``NOTICE_LOCALES`` required).
        headings: Optional heading per locale.
        contacts: The scenario's own contact block; ``None`` takes the group's at the start.
        image: The scenario's image, if any; admins only until a closure copies it.

    Raises:
        DomainError: ``invalid_configuration`` with ``scenario_name``, ``notice_<locale>`` or
            ``heading_<locale>``.
    """

    id: int | None
    organisation_id: int
    group_id: int | None
    name: str
    notices: Mapping[str, str] = field(compare=False)
    headings: Mapping[str, str] = field(default_factory=dict, compare=False)
    contacts: ContactBlock | None = field(default=None, compare=False)
    image: PageImage | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        """Trim and validate the name and texts."""
        name = self.name.strip()
        if not name or len(name) > MAX_NAME_LENGTH or _LINE_BREAKS_OR_CONTROL.search(name):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, "scenario_name")
        notices = {
            loc: clean_notice(self.notices.get(loc, ""), notice_field(loc))
            for loc in NOTICE_LOCALES
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "notices", notices)
        object.__setattr__(self, "headings", clean_headings(self.headings))

    @property
    def is_organisation(self) -> bool:
        """Whether every group of the organisation may use it."""
        return self.group_id is None
