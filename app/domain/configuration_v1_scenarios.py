"""Prepared closure scenarios in a configuration document (ADR 0037 §4, ADR 0099).

A scenario is the content of an emergency closure prepared before it is needed. It belongs to
an organisation or to one group, is addressed by its name within that owner, and may hold one
image with its text alternative. The two kinds differ only in the screen that edits them
(``organisation.closure`` or ``closure.manage``), so one factory builds both. The rules are
those of ``ClosureScenario`` (name, notices and headings), of the contact block, and of
images (``ImageFile.from_upload``, which strips metadata, and ``ImageText``).
"""

from collections.abc import Callable, Mapping

from app.domain.authorization import Permission
from app.domain.closure import heading_field, notice_field
from app.domain.configuration_changes import IntegrityRule, RuleContext
from app.domain.configuration_checks import contacts_check
from app.domain.configuration_schema import (
    Collection,
    Kind,
    Shape,
    binary,
    boolean,
    json_object,
    text,
)
from app.domain.configuration_values import Binary, Value
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import ImageFile, ImageText, alt_field
from app.domain.scenarios import MAX_NAME_LENGTH, ClosureScenario

ORGANISATION_SCENARIO_KIND = "organisation_scenario"
GROUP_SCENARIO_KIND = "group_scenario"
IMAGE_KIND = "scenario_image"
"""The kind names, which other slices read by name."""


def _texts(value: Value, field_of: Callable[[str], str], fallback: str) -> dict[str, str]:
    """Return a JSON object of texts per locale; refuse anything else.

    Raises:
        DomainError: ``invalid_configuration`` naming ``fallback``, or the locale's field.
    """
    if not isinstance(value, dict):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, fallback)
    texts: dict[str, str] = {}
    for locale, item in value.items():
        if not isinstance(item, str):
            raise DomainError(ErrorCode.INVALID_CONFIGURATION, field_of(locale))
        texts[locale] = item
    return texts


def _scenario_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check a scenario as ``ClosureScenario`` does and return it normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``scenario_name``, ``notice_<locale>`` or
            ``heading_<locale>``.
    """
    name = merged.get("name")
    scenario = ClosureScenario(
        None,
        0,
        None,
        name if isinstance(name, str) else "",
        _texts(merged.get("notices"), notice_field, "notices"),
        _texts(merged.get("headings", {}), heading_field, "headings"),
    )
    return {
        **merged,
        "name": scenario.name,
        "notices": dict(scenario.notices),
        "headings": dict(scenario.headings),
    }


def _image_data(value: Value, context: RuleContext) -> Value:
    """Check an image file and return it as it is stored: recognised type, metadata stripped.

    Raises:
        DomainError: ``invalid_configuration`` with ``image``.
    """
    if not isinstance(value, Binary):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "image")
    image = ImageFile.from_upload(value.data)
    return Binary(image.media_type, image.data)


def _image_check(
    merged: Mapping[str, Value], current: Mapping[str, Value] | None, context: RuleContext
) -> Mapping[str, Value]:
    """Check the text alternative of an image as ``ImageText`` does; return it normalised.

    Raises:
        DomainError: ``invalid_configuration`` with ``alt_<locale>``.
    """
    alts = _texts(merged.get("alts", {}), alt_field, "alts")
    text_alternative = ImageText(alts, merged.get("decorative") is True)
    return {**merged, "alts": dict(text_alternative.alts)}


IMAGE = Kind(
    IMAGE_KIND,
    tables=frozenset({"closure_images"}),
    fields=(
        binary("data", check=_image_data),
        json_object("alts", default={}, description="The text alternative per locale."),
        boolean("decorative", default=False),
    ),
    check=_image_check,
    derived=frozenset({"closure_images.media_type", "closure_images.digest"}),
    description="The image of a scenario, metadata stripped, with its text alternative.",
)


def _scenario_kind(name: str, permission: Permission, description: str) -> Kind:
    """Build a scenario kind; the owner (organisation or group) decides the screen."""
    return Kind(
        name,
        key=("name",),
        permission=permission,
        tables=frozenset({"closure_scenarios"}),
        fields=(
            text("name", required=True, max_length=MAX_NAME_LENGTH),
            json_object("notices", description="The notice per locale; nl and en are required."),
            json_object("headings", default={}, description="Optional heading per locale."),
            json_object("contacts", nullable=True, check=contacts_check),
        ),
        collections=(Collection("image", IMAGE, Shape.SINGLE, "The scenario's image."),),
        check=_scenario_check,
        description=description,
    )


ORGANISATION_SCENARIO = _scenario_kind(
    ORGANISATION_SCENARIO_KIND,
    Permission.ORGANISATION_CLOSURE,
    "Closure content every group of the organisation may start from.",
)
GROUP_SCENARIO = _scenario_kind(
    GROUP_SCENARIO_KIND,
    Permission.CLOSURE_MANAGE,
    "Closure content only this group may start from.",
)

ORGANISATION_SCENARIOS = Collection(
    "closure_scenarios", ORGANISATION_SCENARIO, Shape.ENTITIES, "The organisation's scenarios."
)
GROUP_SCENARIOS = Collection(
    "closure_scenarios", GROUP_SCENARIO, Shape.ENTITIES, "The group's own scenarios."
)
"""The ``closure_scenarios`` section of an organisation and of a group."""

INTEGRITY: tuple[IntegrityRule, ...] = ()
"""No rule across objects: a name is unique by being the natural key within its owner, and a
running closure keeps its own copy of the content."""
