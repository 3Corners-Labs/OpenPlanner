"""Web container healthcheck: exit 0 when ``/healthz`` answers on the loopback, 1 otherwise.

Run with ``python -m app.healthcheck`` (the image's ``HEALTHCHECK``). Web serves HTTPS when
``WEB_TLS_CERT_FILE`` is set (ADR 0091 point 3), so the probe follows it. The certificate names
the proxy-facing host, not ``127.0.0.1``, so its name and chain are not checked: the probe sends
no data and only tells whether the process answers.
"""

import os
import ssl
import sys
import urllib.request
from collections.abc import Mapping

LOOPBACK = "127.0.0.1"
PORT = 8000
TIMEOUT_SECONDS = 3


def healthz_url(environ: Mapping[str, str], port: int = PORT) -> str:
    """Return the loopback URL of ``/healthz``, https when ``WEB_TLS_CERT_FILE`` is set."""
    scheme = "https" if environ.get("WEB_TLS_CERT_FILE") else "http"
    return f"{scheme}://{LOOPBACK}:{port}/healthz"


def main(environ: Mapping[str, str] | None = None, port: int = PORT) -> int:
    """Probe web and return the exit code: 0 when it answers 2xx, 1 otherwise."""
    env = os.environ if environ is None else environ
    url = healthz_url(env, port)
    context = None
    if url.startswith("https://"):
        # Loopback liveness only; see the module docstring.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # nosec B501  # nosemgrep
    try:
        # The scheme is http or https by construction, the host the loopback.
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            url, timeout=TIMEOUT_SECONDS, context=context
        ):
            return 0
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
