"""The image form of closures and scenarios (ADR 0037 §3-4): inputs, reading and refusals.

One home for the form that a running closure and a prepared scenario share: the file, the
text alternative per locale and the "decorative" box. The form is multipart; the domain
recognises the type, strips metadata and validates the texts.
"""

from dataclasses import dataclass

from fastapi import Request

from app.admin.forms import Field, FormData
from app.admin.uploads import read_upload
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import (
    ALT_LOCALES,
    IMAGE_TYPES,
    MAX_ALT_LENGTH,
    MAX_IMAGE_BYTES,
    ImageFile,
    ImageText,
    PageImage,
    alt_field,
)
from app.web.errors import FormError
from app.web.locale import RequestLocale

ALT_LABELS: dict[str, str] = {
    "nl": "admin.closure.image_alt_nl",
    "en": "admin.closure.image_alt_en",
}
"""Label key of the image's text alternative per locale (literal for the catalogue test)."""

TEXT_FIELDS = (*(alt_field(code) for code in ALT_LOCALES), "decorative")
"""The text inputs sent with the file."""


def image_fields(form: FormData, locale: RequestLocale, image: PageImage | None) -> list[Field]:
    """Return the inputs of the image form: the file and the text alternative per locale."""
    _ = locale.gettext
    fields = [
        Field(
            "image",
            _("admin.closure.image_file"),
            "file",
            hint=_("admin.closure.image_hint", kb=MAX_IMAGE_BYTES // 1000),
            error=form.error_for("image"),
            required=image is None,
            attributes={"accept": ",".join(IMAGE_TYPES)},
        )
    ]
    for code in ALT_LOCALES:
        name = alt_field(code)
        stored = image.text.alts.get(code, "") if image else ""
        fields.append(
            Field(
                name,
                _(ALT_LABELS[code]),
                value=form.values.get(name, stored),
                hint=_("admin.closure.image_alt_hint"),
                error=form.error_for(name),
                required=False,
                attributes={"maxlength": str(MAX_ALT_LENGTH)},
            )
        )
    return fields


def decorative(form: FormData, image: PageImage | None) -> bool:
    """Return whether the "decorative" box is ticked: as submitted, else as stored."""
    if form.values:
        return form.checked("decorative")
    return bool(image and image.text.decorative)


@dataclass(frozen=True)
class ImageForm:
    """A submitted image form: its text values (for re-rendering) and the file's bytes."""

    form: FormData
    data: bytes

    def parsed(self) -> tuple[ImageText, ImageFile | None]:
        """Return the validated text and the file (``None`` when no file was chosen).

        Raises:
            DomainError: ``invalid_configuration`` with ``alt_<locale>`` or ``image``.
        """
        text = ImageText(
            {code: self.form.text(alt_field(code)) for code in ALT_LOCALES},
            decorative=self.form.checked("decorative"),
        )
        return text, ImageFile.from_upload(self.data) if self.data else None


async def read_image_form(request: Request, locale: RequestLocale) -> ImageForm:
    """Read the multipart image form.

    Raises:
        RejectedRequest: 413 for a body far above the limit, 400 for a malformed one.
    """
    upload = await read_upload(request, "image", MAX_IMAGE_BYTES, TEXT_FIELDS)
    return ImageForm(FormData(upload.values, locale.gettext), upload.data)


def image_refusal(form: FormData, error: DomainError, locale: RequestLocale) -> int:
    """Add the message for a refused image or text to ``form``; return the HTTP status."""
    if error.code is ErrorCode.INVALID_CONFIGURATION and error.field == "image":
        message = locale.gettext("admin.form.error.image", kb=MAX_IMAGE_BYTES // 1000)
        form.errors.append(FormError("image", message))
        return 422
    form.refusal(error)
    return 422 if error.code is ErrorCode.INVALID_CONFIGURATION else 0
