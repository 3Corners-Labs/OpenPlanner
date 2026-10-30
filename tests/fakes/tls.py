"""Fakes of the certificate ports: probe (ADR 0064), PEM reader and delivery (ADR 0068)."""

from app.domain.errors import DomainError, ErrorCode
from app.domain.own_certificate import HostKeys, ParsedChain, ParsedKey
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
    """Returns the parsed chain and key the test put in; ``unreadable`` makes it refuse."""

    def __init__(
        self,
        chain: ParsedChain | None = None,
        key: ParsedKey | None = None,
        *,
        unreadable: bool = False,
    ) -> None:
        self.chain = chain
        self.key = key
        self.unreadable = unreadable
        self.read_sizes: list[int] = []

    def read_chain(self, chain: bytes) -> ParsedChain:
        self.read_sizes.append(len(chain))
        if self.unreadable or self.chain is None:
            raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "chain")
        return self.chain

    def read_key(self, key: bytes) -> ParsedKey:
        self.read_sizes.append(len(key))
        if self.unreadable or self.key is None:
            raise DomainError(ErrorCode.TLS_CERTIFICATE_UNREADABLE, "key")
        return self.key


class FakeCertificateVolume:
    """An in-memory volume: public keys by name, the pending request, the served pair."""

    def __init__(self) -> None:
        self.current: bytes | None = None
        self.current_pem: str | None = None
        self.pending: bytes | None = None
        self.pending_csr: str | None = None
        self.published: str | None = None
        self.calls: list[str] = []
        self.unwritable = False
        self.generated = 0

    def keys(self) -> HostKeys:
        return HostKeys(self.current, self.pending, self.pending_csr)

    def generate_pending(self, host: str) -> str:
        self._writable()
        self.generated += 1
        self.pending = f"public-{self.generated}".encode()
        self.pending_csr = f"-----BEGIN CERTIFICATE REQUEST-----\n{host}#{self.generated}\n"
        self.calls.append(f"generate:{host}")
        return self.pending_csr

    def promote_pending(self) -> None:
        self.current, self.pending, self.pending_csr = self.pending, None, None
        self.calls.append("promote")

    def import_key(self, key_pem: str) -> None:
        self._writable()
        self.current_pem = key_pem
        self.current = b"imported"
        self.pending = self.pending_csr = None
        self.calls.append("import")

    def publish(self, chain_pem: str, fingerprint: str) -> None:
        self.published = fingerprint
        self.calls.append(f"publish:{fingerprint}")

    def withdraw(self) -> None:
        self.published = None
        self.calls.append("withdraw")

    def _writable(self) -> None:
        if self.unwritable:
            raise DomainError(ErrorCode.TLS_VOLUME_UNWRITABLE)
