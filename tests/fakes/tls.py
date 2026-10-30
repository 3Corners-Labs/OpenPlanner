"""A ``CertificateProbe`` whose answer the test sets."""

from app.domain.tls import ProbeResult


class FakeCertificateProbe:
    """Returns the result the test put in ``result`` and remembers every address asked."""

    def __init__(self, result: ProbeResult | None = None) -> None:
        self.result = result or ProbeResult(error="unreachable")
        self.asked: list[tuple[str, int]] = []

    async def probe(self, host: str, port: int) -> ProbeResult:
        self.asked.append((host, port))
        return self.result
