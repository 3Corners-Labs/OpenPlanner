"""The shared part of the application's own cookies: name, path and ``Secure`` by address.

Two cookies use it, each independent of the other: the admin session (``op_admin``, ADR 0098)
and the participant link session (``op_link``, ADR 0093). The rule is the same for both and is
a fixed security rule, not a setting: on an ``https://`` address the cookie carries the
``__Host-`` prefix, ``Path=/``, ``Secure`` and no ``Domain``, whatever ``ENVIRONMENT`` says.
Over ``http://`` (development) browsers refuse a prefixed cookie without ``Secure``, so the
plain name stays with a narrower path. Every cookie is ``HttpOnly`` and ``SameSite=Lax``.
"""

from dataclasses import dataclass

from starlette.responses import Response

HOST_PREFIX = "__Host-"


@dataclass(frozen=True)
class CookieSpec:
    """The name and attributes of one of the application's cookies.

    Attributes:
        name: ``__Host-<base>`` on https, ``<base>`` over plain http.
        path: ``/`` for the prefixed name (the prefix requires it), the plain path otherwise.
        secure: Whether the cookie is sent only over https.
    """

    name: str
    path: str
    secure: bool


def cookie_for(public_base_url: str, base: str, plain_path: str) -> CookieSpec:
    """Return the cookie ``base`` to use for the address the installation is served on.

    Args:
        public_base_url: The ``PUBLIC_BASE_URL`` setting.
        base: The cookie name without prefix, for example ``op_link``.
        plain_path: The path over plain http, as narrow as the cookie's use allows.

    Returns:
        The name, path and ``Secure`` flag.
    """
    if public_base_url.lower().startswith("https://"):
        return CookieSpec(f"{HOST_PREFIX}{base}", "/", True)
    return CookieSpec(base, plain_path, False)


def set_cookie(response: Response, spec: CookieSpec, value: str, max_age: int) -> None:
    """Set the cookie on ``response``: ``HttpOnly``, ``SameSite=Lax``, no ``Domain``."""
    response.set_cookie(
        spec.name,
        value,
        max_age=max_age,
        path=spec.path,
        secure=spec.secure,
        httponly=True,
        samesite="lax",
    )
