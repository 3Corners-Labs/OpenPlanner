"""Reading uploaded files from a multipart admin form (branding, closure images, certificates).

The declared body size is checked before anything is read: a body far above the limit is
refused with 413 unread (ADR 0020, ADR 0037 §3). The file itself is read up to one byte past
the limit, so the domain can refuse a file that is too large without holding all of it.
"""

from dataclasses import dataclass, field

from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.web.protection import RejectedRequest

MAX_UPLOAD_OVERHEAD = 16 * 1024
"""Room for multipart headers and the form's text fields around an upload."""


@dataclass(frozen=True)
class Upload:
    """What a multipart form carried: the file (empty when none was chosen) and text fields."""

    data: bytes
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Uploads:
    """Several uploaded files by field name (empty when none was chosen) and text fields."""

    files: dict[str, bytes]
    values: dict[str, str] = field(default_factory=dict)


async def read_upload(
    request: Request, name: str, limit: int, fields: tuple[str, ...] = ()
) -> Upload:
    """Read the one uploaded file ``name`` and the text ``fields`` next to it.

    Raises:
        RejectedRequest: 413 when the declared body is far above the limit (refused before
            anything is read); 400 for a malformed multipart body.
    """
    uploads = await read_uploads(request, (name,), limit, fields)
    return Upload(uploads.files[name], uploads.values)


async def read_uploads(
    request: Request, names: tuple[str, ...], limit: int, fields: tuple[str, ...] = ()
) -> Uploads:
    """Read the uploaded files ``names`` (each at most ``limit`` bytes) and the text ``fields``.

    Raises:
        RejectedRequest: 413 when the declared body is far above the limit (refused before
            anything is read); 400 for a malformed multipart body.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > len(names) * limit + MAX_UPLOAD_OVERHEAD:
        raise RejectedRequest(413)
    try:
        async with request.form(
            max_files=len(names), max_fields=len(fields) + 2, max_part_size=limit + 1
        ) as form:
            values = {f: v for f in fields if isinstance(v := form.get(f), str)}
            files: dict[str, bytes] = {}
            for name in names:
                upload = form.get(name)
                files[name] = (
                    await upload.read(limit + 1) if isinstance(upload, UploadFile) else b""
                )
            return Uploads(files, values)
    except MultiPartException as exc:
        raise RejectedRequest(400) from exc
