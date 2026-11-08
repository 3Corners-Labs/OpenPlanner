"""Subjects and routing attributes next to the group, and their mapping to the contact center.

ADR 0081. A group may give participants a list of *subjects* to choose from (point 2) and
switch on *routing attributes* from a closed catalogue that ships with OpenPlanner (point 3).
The participant's choices are stored on the booking as codes, never as free text, and an
organisation admin may map each subject and attribute to a *routing variable* of the contact
center (point 5). Only mapped values leave the system, and only as ``{name: value}`` pairs of
codes the organisation or OpenPlanner wrote (point 6).

Nothing here is personal data: a subject or an attribute value is one entry of a fixed list.
The catalogue is code on purpose; adding an attribute is an ADR, because each one must be
shown to be non-personal.
"""

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.errors import DomainError, ErrorCode

SUBJECT_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
"""A subject's stable code (ADR 0081 point 2); reporting (ADR 0117) cuts by it."""
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
"""A routing variable's name: what the client's contact-center flow reads (ADR 0081 point 5)."""
VARIABLE_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
"""A value sent for one code: a code itself, so nothing typed can carry free text."""
MAX_LABEL_LENGTH = 80
REQUIRED_LOCALES = ("nl", "en")
"""Every subject label needs these (CLAUDE.md: nl default, en required)."""
RESERVED_VARIABLES = frozenset({"openplannerGroup"})
"""Variable names OpenPlanner itself sends to the contact center; a mapping may not take them."""
SUBJECT_SOURCE = "subject"
"""The mapping source of the group's subjects; the other sources are the attribute keys."""
ANSWER_PREFIX = "_"
"""Answers to the routing questions arrive keyed ``_subject`` and ``_<attribute>``.

A custom form field's key (ADR 0055) starts with a letter, so these never collide with one."""

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class Attribute(StrEnum):
    """The routing attributes OpenPlanner knows (ADR 0081 point 3); a new one needs an ADR."""

    PREFERRED_LANGUAGE = "preferred_language"
    CHANNEL_PREFERENCE = "channel_preference"


ATTRIBUTE_VALUES: Mapping[Attribute, tuple[str, ...]] = {
    Attribute.PREFERRED_LANGUAGE: ("nl", "en"),
    Attribute.CHANNEL_PREFERENCE: ("video", "phone"),
}
"""The codes each attribute may take. ``preferred_language`` holds the locales with a
catalogue; a test keeps it equal to the i18n module's list."""


def _invalid(name: str) -> DomainError:
    """Return the configuration error for ``name``."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, name)


def _check_label(text: str, name: str) -> str:
    """Return ``text`` stripped, or refuse a blank, long or control-character label."""
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_LABEL_LENGTH or _CONTROL_CHARS.search(stripped):
        raise _invalid(name)
    return stripped


@dataclass(frozen=True)
class Subject:
    """One subject a participant may choose (ADR 0081 point 2).

    Attributes:
        code: Stable code, ``[a-z0-9][a-z0-9-]{0,39}``; stored on the booking and reported on.
        labels: The label per locale; ``nl`` and ``en`` are required.
    """

    code: str
    labels: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        """Validate the code and the labels."""
        if not SUBJECT_CODE.fullmatch(self.code):
            raise _invalid("subject_code")
        locales = {locale for locale, _ in self.labels}
        if any(locale not in locales for locale in REQUIRED_LOCALES):
            raise _invalid("subject_label")
        for locale, text in self.labels:
            if text != _check_label(text, f"subject_label_{locale}"):
                raise _invalid(f"subject_label_{locale}")

    @classmethod
    def create(cls, code: str, labels: Mapping[str, str]) -> "Subject":
        """Return a subject from raw input, labels stripped."""
        return cls(
            code.strip(),
            tuple(sorted((k, _check_label(v, f"subject_label_{k}")) for k, v in labels.items())),
        )

    def label(self, locale: str) -> str:
        """Return the label in ``locale``, Dutch when the locale has none."""
        by_locale = dict(self.labels)
        return by_locale.get(locale) or by_locale[REQUIRED_LOCALES[0]]


@dataclass(frozen=True)
class RouteVariable:
    """The routing variable one source maps to (ADR 0081 point 5).

    Attributes:
        name: The variable the client's flow reads.
        values: The value sent per code; a code without an entry is not sent.
    """

    name: str
    values: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Validate the name and every value."""
        if not VARIABLE_NAME.fullmatch(self.name) or self.name in RESERVED_VARIABLES:
            raise _invalid("variable_name")
        if any(not VARIABLE_VALUE.fullmatch(value) for _, value in self.values):
            raise _invalid("variable_value")

    def value(self, code: str) -> str | None:
        """Return the value sent for ``code``, or ``None`` when it is unmapped."""
        return dict(self.values).get(code)


@dataclass(frozen=True)
class RoutingChoice:
    """What a participant chose: the subject code and attribute codes (never free text)."""

    subject: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    @property
    def empty(self) -> bool:
        """Whether nothing was chosen."""
        return self.subject is None and not self.attributes

    def attribute(self, attribute: Attribute) -> str | None:
        """Return the code chosen for ``attribute``, if any."""
        return dict(self.attributes).get(attribute.value)


@dataclass(frozen=True)
class GroupRouting:
    """A group's subjects, switched-on attributes and routing mapping (ADR 0081).

    Attributes:
        subjects: The subjects in the order the form shows them.
        attributes: The switched-on attributes, in catalogue order.
        mapping: Per source (``subject`` or an attribute key) the variable it maps to.
    """

    subjects: tuple[Subject, ...] = ()
    attributes: tuple[Attribute, ...] = ()
    mapping: tuple[tuple[str, RouteVariable], ...] = field(default=())

    def __post_init__(self) -> None:
        """Refuse duplicate codes, unknown sources, foreign codes and duplicate variables."""
        codes = [s.code for s in self.subjects]
        if len(codes) != len(set(codes)):
            raise _invalid("subject_code")
        if len(set(self.attributes)) != len(self.attributes):
            raise _invalid("attributes")
        names = [variable.name for _, variable in self.mapping]
        if len(names) != len(set(names)):
            raise _invalid("variable_name")
        sources = [source for source, _ in self.mapping]
        if len(sources) != len(set(sources)):
            raise _invalid("mapping")
        for source, variable in self.mapping:
            allowed = self.codes(source)
            if not allowed or any(code not in allowed for code, _ in variable.values):
                raise _invalid("mapping")

    @property
    def asks_subject(self) -> bool:
        """Whether the form asks the participant for a subject."""
        return bool(self.subjects)

    @property
    def sources(self) -> tuple[str, ...]:
        """The sources that can be mapped: the subjects (if any) and the attributes."""
        head = (SUBJECT_SOURCE,) if self.subjects else ()
        return head + tuple(a.value for a in self.attributes)

    def codes(self, source: str) -> tuple[str, ...]:
        """Return the codes a source may take; empty for a source the group does not use."""
        if source == SUBJECT_SOURCE:
            return tuple(s.code for s in self.subjects)
        attribute = next((a for a in self.attributes if a.value == source), None)
        return ATTRIBUTE_VALUES[attribute] if attribute else ()

    def subject(self, code: str | None) -> Subject | None:
        """Return the subject with ``code``, or ``None``."""
        return next((s for s in self.subjects if s.code == code), None)

    def variable(self, source: str) -> RouteVariable | None:
        """Return the variable ``source`` maps to, or ``None``."""
        return dict(self.mapping).get(source)

    def choose(self, answers: Mapping[str, str | None]) -> RoutingChoice:
        """Validate the participant's answers (keyed ``_subject``, ``_<attribute>``).

        Every question the group asks must be answered with one of its codes; anything else,
        including a value typed into a crafted form, is refused (ADR 0081, threat model).

        Raises:
            DomainError: ``invalid_answer`` with the answer key (never the value).
        """
        subject = None
        if self.subjects:
            subject = self._answer(answers, SUBJECT_SOURCE)
        chosen = tuple((a.value, self._answer(answers, a.value)) for a in self.attributes)
        return RoutingChoice(subject, chosen)

    def _answer(self, answers: Mapping[str, str | None], source: str) -> str:
        """Return the answer for ``source`` if it is one of its codes."""
        key = f"{ANSWER_PREFIX}{source}"
        value = (answers.get(key) or "").strip()
        if value not in self.codes(source):
            raise DomainError(ErrorCode.INVALID_ANSWER, key)
        return value

    def variables(self, choice: RoutingChoice | None) -> dict[str, str]:
        """Return the routing variables for ``choice``: mapped codes only (ADR 0081 point 5).

        A source that is unmapped, a code without a value and a code the group no longer has
        are left out; nothing else of the booking can get in.
        """
        if choice is None:
            return {}
        picked = dict(choice.attributes)
        if choice.subject is not None:
            picked[SUBJECT_SOURCE] = choice.subject
        sent: dict[str, str] = {}
        for source, variable in self.mapping:
            code = picked.get(source)
            if code is None or code not in self.codes(source):
                continue
            value = variable.value(code)
            if value is not None:
                sent[variable.name] = value
        return sent

    def with_subjects(self, subjects: Iterable[Subject]) -> "GroupRouting":
        """Return a copy with ``subjects``; mappings of removed codes are dropped."""
        return _rebuild(tuple(subjects), self.attributes, self.mapping)

    def with_attributes(self, attributes: Collection[Attribute]) -> "GroupRouting":
        """Return a copy with the attributes switched on, in catalogue order."""
        ordered = tuple(a for a in Attribute if a in attributes)
        return _rebuild(self.subjects, ordered, self.mapping)

    def with_mapping(self, source: str, variable: RouteVariable | None) -> "GroupRouting":
        """Return a copy where ``source`` maps to ``variable`` (``None`` removes it)."""
        if source not in self.sources:
            raise _invalid("mapping")
        kept = tuple((s, v) for s, v in self.mapping if s != source)
        added = ((source, variable),) if variable is not None else ()
        order = {s: i for i, s in enumerate(self.sources)}
        mapping = tuple(sorted(kept + added, key=lambda item: order[item[0]]))
        return GroupRouting(self.subjects, self.attributes, mapping)


def _rebuild(
    subjects: tuple[Subject, ...],
    attributes: tuple[Attribute, ...],
    mapping: tuple[tuple[str, RouteVariable], ...],
) -> GroupRouting:
    """Return the routing, keeping only the mapping entries that are still valid."""
    base = GroupRouting(subjects, attributes)
    kept: list[tuple[str, RouteVariable]] = []
    for source, variable in mapping:
        allowed = base.codes(source)
        if not allowed:
            continue
        values = tuple((c, v) for c, v in variable.values if c in allowed)
        kept.append((source, RouteVariable(variable.name, values)))
    return GroupRouting(subjects, attributes, tuple(kept))


def check_subject_count(routing: GroupRouting, maximum: int) -> None:
    """Refuse more subjects than the platform allows (``max_subjects_per_group``).

    Raises:
        DomainError: ``invalid_configuration`` on ``subjects``.
    """
    if len(routing.subjects) > maximum:
        raise _invalid("subjects")
