"""The built ``<gov-booking>`` script (ADR 0035 §5): where it lives, its bytes, its SRI hash.

esbuild writes ``widget/dist/gov-booking.js`` in CI and in the image build; a checkout without
a build has no bundle, and the script route then answers 404. The admin portal shows the
same hash in the snippet hosts copy, so a host can pin the script with ``integrity``.
"""

import base64
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BUNDLE_PATH = Path(__file__).resolve().parents[2] / "widget" / "dist" / "gov-booking.js"
SCRIPT_URL_PATH = "/widget/v1/gov-booking.js"
"""Major version in the path: a breaking change to the element's attributes gets ``v2``."""


@dataclass(frozen=True)
class Bundle:
    """The script and its Subresource Integrity value (``sha384-…``)."""

    data: bytes
    integrity: str


@lru_cache(maxsize=4)
def _read(path: Path, mtime_ns: int, size: int) -> Bundle:
    """Read and hash the bundle; cached per file version (path, mtime, size)."""
    data = path.read_bytes()
    digest = base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
    return Bundle(data, f"sha384-{digest}")


def load_bundle(path: Path | None = None) -> Bundle | None:
    """Return the built script, or ``None`` when this installation has no build."""
    target = path or BUNDLE_PATH
    try:
        stat = target.stat()
    except FileNotFoundError:
        return None
    return _read(target, stat.st_mtime_ns, stat.st_size)
