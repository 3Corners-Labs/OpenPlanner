"""What the screens of the skill catalogue show of a skill (ADR 0121 points 5 and 11).

View models and form fields shared by the combined *Skills and capacity* page
(``app.admin.skills_capacity_page``) and the pages of one skill (``app.admin.skill_routes``).
"""

from dataclasses import dataclass

from app.admin.forms import Field, FormData
from app.admin.skill_inputs import other_label, skill_label
from app.domain.skill_service import SkillLine
from app.web.locale import RequestLocale


@dataclass(frozen=True)
class SkillRowView:
    """One skill of the catalogue as the list and the confirmation page show it."""

    id: int
    code: str
    label: str
    other_label: str
    staff: int
    pools: int
    flows: int
    archived: bool
    contact_center_code: str = ""


def skill_row(line: SkillLine, locale: RequestLocale) -> SkillRowView:
    """Translate a skill with its use for the templates."""
    skill = line.skill
    return SkillRowView(
        skill.id or 0,
        skill.code,
        skill_label(skill, locale.code),
        other_label(skill, locale.code),
        line.usage.staff,
        line.usage.pools,
        line.usage.flows,
        skill.archived,
        skill.contact_center_code or "",
    )


def label_fields(form: FormData, locale: RequestLocale, line: SkillLine | None) -> list[Field]:
    """Return the Dutch and English label inputs, filled from the form or the stored skill."""
    _ = locale.gettext
    stored = line.skill.labels if line else {}
    texts = {"nl": _("admin.skills.label_nl"), "en": _("admin.skills.label_en")}
    return [
        Field(
            f"label_{code}",
            text,
            value=form.values.get(f"label_{code}", stored.get(code, "")),
            error=form.error_for(f"label_{code}"),
            attributes={"maxlength": "80", "autocomplete": "off"},
        )
        for code, text in texts.items()
    ]


def contact_center_field(form: FormData, locale: RequestLocale, line: SkillLine) -> Field:
    """Return the input of the skill's contact-center code (ADR 0121 step 4b); blank: not mapped."""
    _ = locale.gettext
    stored = line.skill.contact_center_code or ""
    return Field(
        "contact_center_code",
        _("admin.skills.contact_center_code"),
        value=form.values.get("contact_center_code", stored),
        hint=_("admin.skills.contact_center_code_hint"),
        error=form.error_for("contact_center_code"),
        required=False,
        attributes={"maxlength": "64", "spellcheck": "false", "autocapitalize": "none"},
    )


def code_field(form: FormData, locale: RequestLocale, line: SkillLine | None) -> Field:
    """Return the code input: typed for a new skill, shown read-only for a stored one."""
    _ = locale.gettext
    if line is not None:
        return Field(
            "skill_code",
            _("admin.skills.code"),
            value=line.skill.code,
            readonly=True,
            hint=_("admin.skills.code_fixed"),
        )
    return Field(
        "skill_code",
        _("admin.skills.code"),
        value=form.text("skill_code"),
        hint=_("admin.skills.code_hint"),
        error=form.error_for("skill_code"),
        attributes={"maxlength": "40", "spellcheck": "false", "autocapitalize": "none"},
    )
