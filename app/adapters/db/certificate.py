"""SQL queries of the certificate check (ADR 0064).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. The table holds at most one row: the latest check of the public address's
certificate. A stored finding or trust code that no longer parses (a hand-edited row) is read
as ``unreachable`` rather than failing the status page.
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import CertificateCheckRow
from app.domain.tls import Certificate, CertificateCheck, Finding, Trust

_CHECK_ID = 1


def _finding(raw: str) -> Finding:
    """Parse a stored finding; anything unknown counts as unreachable."""
    try:
        return Finding(raw)
    except ValueError:
        return Finding.UNREACHABLE


def _trust(raw: str | None) -> Trust | None:
    """Parse a stored trust verdict; anything unknown counts as none."""
    try:
        return Trust(raw) if raw else None
    except ValueError:
        return None


def _certificate(row: CertificateCheckRow) -> Certificate | None:
    """Rebuild the stored certificate; without validity dates there was none."""
    if row.not_before is None or row.not_after is None:
        return None
    names = json.loads(row.names or "[]")
    return Certificate(
        row.subject or "",
        row.issuer or "",
        row.not_before,
        row.not_after,
        tuple(str(name) for name in names),
    )


class CertificateQueries:
    """The certificate check's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    async def get_certificate_check(self) -> CertificateCheck | None:
        """Return the latest check, if there was one."""
        row = await self._session.get(CertificateCheckRow, _CHECK_ID)
        if row is None:
            return None
        return CertificateCheck(
            row.checked_at,
            row.host,
            _finding(row.finding),
            _certificate(row),
            _trust(row.trust),
            row.error,
        )

    async def save_certificate_check(self, check: CertificateCheck) -> None:
        """Replace the single row."""
        certificate = check.certificate
        await self._session.merge(
            CertificateCheckRow(
                id=_CHECK_ID,
                checked_at=check.checked_at,
                host=check.host,
                finding=check.finding.value,
                trust=check.trust.value if check.trust else None,
                error=check.error,
                subject=certificate.subject if certificate else None,
                issuer=certificate.issuer if certificate else None,
                not_before=certificate.not_before if certificate else None,
                not_after=certificate.not_after if certificate else None,
                names=json.dumps(list(certificate.names)) if certificate else "[]",
            )
        )
        await self._session.flush()
