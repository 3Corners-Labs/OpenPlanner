"""Fakes of the certificate ports: probe (ADR 0064), PEM reader and delivery (ADR 0066)."""

from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import PemPair
from app.domain.tls import ProbeResult


class FakeCertificateProbe:
    """Returns the result the test put in ``result`` and remembers every address asked."""

    def __init__(self, result: ProbeResult | None = None) -> None:
        self.result = result or ProbeResult(error="unreachable")
        self.asked: list[tuple[str, int]] = []

    async def probe(self, host: str, port: int) -> ProbeResult:
        self.asked.append((host, port))
        return self.result


class FakePemReader:
    """Returns the pair the test put in ``pair``; ``unreadable`` makes it refuse instead."""

    def __init__(self, pair: PemPair | None = None, *, unreadable: bool = False) -> None:
        self.pair = pair
        self.unreadable = unreadable
        self.read_sizes: list[tuple[int, int]] = []

    def read(self, chain: bytes, key: bytes) -> PemPair:
        self.read_sizes.append((len(chain), len(key)))
        if self.unreadable or self.pair is None:
            raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain")
        return self.pair


class FakeCertificateDelivery:
    """Remembers the fingerprint published last, or ``None`` after a withdrawal."""

    def __init__(self) -> None:
        self.published: str | None = None
        self.calls: list[str] = []

    async def publish(self, fingerprint: str) -> None:
        self.published = fingerprint
        self.calls.append(f"publish:{fingerprint}")

    async def withdraw(self) -> None:
        self.published = None
        self.calls.append("withdraw")
