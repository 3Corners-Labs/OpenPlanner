"""The host's trust in the proxy's internal certificate authority (ADR 0112).

With ``TLS_INTERNAL=true`` the proxy issues its certificates from Caddy's own authority and
publishes only the public root to the volume ``internal-ca``. The certificate probe and the
round trip to the public address then verify against the system's CA bundle *plus* that root,
so an internal certificate is ``ok`` while a certificate that chains to neither stays
``untrusted``. Without the file nothing changes.
"""

import ssl
from pathlib import Path

import httpx

from app.adapters.instance_probe import HttpxInstanceProbe
from app.adapters.tls_probe import SslCertificateProbe
from app.config import Settings


def root_file(settings: Settings) -> Path | None:
    """Return the internal root certificate when the proxy has published it, else ``None``."""
    path = settings.tls_internal_ca_file
    return path if path.is_file() else None


def certificate_probe(settings: Settings) -> SslCertificateProbe:
    """Return the certificate probe that also trusts the internal root, when there is one.

    The file is looked up at each probe: on a fresh installation the proxy publishes it only
    after web and worker are up, and a probe that decided once would stay untrusting.
    """

    def root() -> str | None:
        found = root_file(settings)
        return str(found) if found else None

    return SslCertificateProbe(extra_cafile=root)


def instance_probe(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> HttpxInstanceProbe:
    """Return the address probe; a given ``transport`` (tests) wins over the internal root.

    Without a given transport the internal root is looked up at each probe, for the same
    reason as in ``certificate_probe``.
    """
    if transport is not None:
        return HttpxInstanceProbe(transport=transport)

    def with_root() -> httpx.AsyncBaseTransport | None:
        root = root_file(settings)
        if root is None:
            return None
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(root))
        return httpx.AsyncHTTPTransport(verify=context)

    return HttpxInstanceProbe(transport_factory=with_root)
