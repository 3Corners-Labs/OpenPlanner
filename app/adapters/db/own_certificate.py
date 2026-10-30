"""SQL queries of the own certificate uploaded in the portal (ADR 0066).

A mixin of ``SqlTransaction``. One row at most: the chain as text, the key sealed under
``tls:own_certificate`` with the transaction's ``SecretBox`` (ADR 0008). The domain reads the
record without the key (``get_own_certificate``, on the port); only the delivery adapter opens
the key, through ``own_certificate_key``, which is not part of the port.
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.models import OwnCertificateRow
from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import OwnCertificate
from app.domain.tls import Certificate

_ROW_ID = 1
KEY_NAME = "tls:own_certificate"
"""The record name the key is sealed under (associated data, ADR 0008)."""


class OwnCertificateQueries:
    """The own certificate's part of ``SqlTransaction``; needs ``_session`` and ``_box``."""

    _session: AsyncSession
    _box: SecretBox | None

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
            row.uploaded_at,
        )

    async def save_own_certificate(self, certificate: OwnCertificate, key_pem: str) -> None:
        """Seal the key and store or replace the single row.

        Raises:
            DomainError: ``setting_no_key`` without a settings key.
        """
        if self._box is None:
            raise DomainError(ErrorCode.SETTING_NO_KEY)
        nonce, ciphertext = self._box.seal(KEY_NAME, key_pem.encode())
        leaf = certificate.certificate
        await self._session.merge(
            OwnCertificateRow(
                id=_ROW_ID,
                chain_pem=certificate.chain_pem,
                key_nonce=nonce,
                key_ciphertext=ciphertext,
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

    async def own_certificate_key(self) -> str | None:
        """Return the opened private key for the delivery adapter; not part of the port.

        ``None`` without a row or without a settings key. A key sealed under another settings
        key raises ``cryptography.exceptions.InvalidTag``, as every secret does.
        """
        row = await self._session.get(OwnCertificateRow, _ROW_ID)
        if row is None or self._box is None:
            return None
        return self._box.open(KEY_NAME, row.key_nonce, row.key_ciphertext).decode()
