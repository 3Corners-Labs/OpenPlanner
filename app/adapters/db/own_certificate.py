"""SQL queries of the own certificate uploaded in the portal (ADR 0070).

A mixin of ``SqlTransaction``. One row at most: the chain (public), the leaf's public key and
the public data the page shows. No private key is ever stored here: it lives on the volume
the proxy reads (``app.adapters.certificate_volume``), so a database backup never carries it.
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import OwnCertificateRow
from app.domain.own_certificate import OwnCertificate
from app.domain.tls import Certificate

_ROW_ID = 1


class OwnCertificateQueries:
    """The own certificate's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_own_certificate(self) -> OwnCertificate | None:
        """Return the stored certificate's public data and chain; ``None`` without one."""
        row = await self._session.get(OwnCertificateRow, _ROW_ID)
        if row is None:
            return None
        names = tuple(str(name) for name in json.loads(row.names or "[]"))
        return OwnCertificate(
            Certificate(row.subject, row.issuer, row.not_before, row.not_after, names),
            row.fingerprint,
            row.chain_pem,
            row.public_key,
            row.uploaded_at,
        )

    async def save_own_certificate(self, certificate: OwnCertificate) -> None:
        """Store or replace the single row."""
        leaf = certificate.certificate
        await self._session.merge(
            OwnCertificateRow(
                id=_ROW_ID,
                chain_pem=certificate.chain_pem,
                public_key=certificate.public_key,
                fingerprint=certificate.fingerprint,
                subject=leaf.subject,
                issuer=leaf.issuer,
                not_before=leaf.not_before,
                not_after=leaf.not_after,
                names=json.dumps(list(leaf.names)),
                uploaded_at=certificate.uploaded_at,
            )
        )
        await self._session.flush()

    async def delete_own_certificate(self) -> bool:
        """Remove the row; return whether there was one."""
        row = await self._session.get(OwnCertificateRow, _ROW_ID)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
