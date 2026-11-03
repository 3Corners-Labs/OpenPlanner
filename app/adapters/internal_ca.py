"""The host's trust in the proxy's internal certificate authority (ADR 0111).

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
    """Return the certificate probe that also trusts the internal root, when there is one."""
    root = root_file(settings)
    return SslCertificateProbe(extra_cafile=str(root) if root else None)


def instance_probe(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> HttpxInstanceProbe:
    """Return the address probe; a given ``transport`` (tests) wins over the internal root."""
    root = root_file(settings)
    if transport is None and root is not None:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(root))
        transport = httpx.AsyncHTTPTransport(verify=context)
    return HttpxInstanceProbe(transport=transport)
