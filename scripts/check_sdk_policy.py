"""Scan a placed SDK file for what the call page's policy forbids or lacks (ADR 0123 step 4c).

The call page's response policy (``app.web.security_policy.CALL_POLICY``) was written before the
real file was ever served under it. This script is the part of that check that needs no browser
and no meeting: it reads the vendored file's text and reports

- ``host``: a URL whose host the policy's ``connect-src`` does not name (``CALL_HOSTS``). A bundle
  holds many addresses that it never contacts (documentation, namespaces); each one is a
  candidate for a person to judge, not proof of a request;
- ``blob_worker``: a worker started from a blob, which ``worker-src 'self'`` refuses;
- ``wasm``: use of WebAssembly, which needs ``wasm-unsafe-eval`` that the policy does not have;
- ``eval``: ``eval(`` or ``new Function(``, which need ``unsafe-eval``.

What it cannot show is whether the file *runs* any of these on the path of a call: only a join
into a real meeting under the page's headers, with the browser's console open for policy
violations, shows that. A clean report here narrows that run; it does not replace it.

    python3 -m scripts.check_sdk_policy [FILE]

Without ``FILE`` the file of the vendored manifest is read. When it is not placed the script
says so and exits 0 (the file is not committed, ADR 0123 point 3). Exit 1 when anything is
found; the report names the kind and a short excerpt, never the file's whole text. Standard
library only.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.web.security_policy import CALL_HOSTS
from scripts.vendor import ROOT, manifests

URL = re.compile(r"\b(https?|wss?)://([A-Za-z0-9.-]+)", re.IGNORECASE)
BLOB_WORKER = re.compile(r"new\s+(?:Shared)?Worker\s*\([^)]{0,200}(?:blob:|createObjectURL)")
WASM = re.compile(r"WebAssembly\s*\.\s*(?:instantiate|compile|Module|Instance|validate)|\.wasm\b")
EVAL = re.compile(r"(?<![\w.])eval\s*\(|new\s+Function\s*\(")
IGNORED_HOSTS = frozenset({"www.w3.org", "w3.org", "localhost", "127.0.0.1"})
"""Hosts that are namespaces or the machine itself: never a request to someone else."""
EXCERPT = 60
"""Characters of a finding's excerpt on each side of the match."""


@dataclass(frozen=True)
class Finding:
    """One thing the policy would refuse or lacks.

    Attributes:
        kind: ``host``, ``blob_worker``, ``wasm`` or ``eval``.
        detail: The host, or a short excerpt around the match.
    """

    kind: str
    detail: str


def host_allowed(scheme: str, host: str) -> bool:
    """Return whether ``connect-src`` of the call page names ``scheme://host``.

    A source ``https://*.example.com`` allows subdomains of ``example.com`` over ``https`` only,
    never the bare name and never another scheme (the browser's own rule for a host source).
    """
    for source in CALL_HOSTS:
        allowed_scheme, _, pattern = source.partition("://")
        if scheme.lower() != allowed_scheme:
            continue
        if pattern.startswith("*.") and host.lower().endswith(pattern.removeprefix("*")):
            return True
    return False


def scan(source: str) -> list[Finding]:
    """Return the findings in ``source``, one per host and per match, in order of appearance."""
    found: list[Finding] = []
    seen: set[str] = set()
    for match in URL.finditer(source):
        scheme, host = match.group(1), match.group(2).rstrip(".")
        if host.lower() in IGNORED_HOSTS or host_allowed(scheme, host) or host in seen:
            continue
        seen.add(host)
        found.append(Finding("host", f"{scheme.lower()}://{host}"))
    for kind, pattern in (("blob_worker", BLOB_WORKER), ("wasm", WASM), ("eval", EVAL)):
        for match in pattern.finditer(source):
            start, end = max(0, match.start() - EXCERPT), match.end() + EXCERPT
            found.append(Finding(kind, " ".join(source[start:end].split())))
    return found


def report(findings: list[Finding]) -> str:
    """Return the report: one line per finding, a count per kind, and what this cannot show."""
    lines = [f"{f.kind:<12} {f.detail}" for f in findings]
    kinds = sorted({f.kind for f in findings})
    lines.append(
        "found: "
        + (", ".join(f"{sum(f.kind == k for f in findings)} {k}" for k in kinds) or "nothing")
    )
    lines.append(
        "only a join into a real meeting under the page's headers shows whether the file runs "
        "any of this (see docs/runbook.md, the call page's SDK)"
    )
    return "\n".join(lines)


def main(argv: list[str], root: Path = ROOT) -> int:
    """Scan the file in ``argv`` or the vendored one; return the exit status."""
    if len(argv) > 2:
        print("usage: python3 -m scripts.check_sdk_policy [FILE]", file=sys.stderr)
        return 2
    if len(argv) == 2:
        path = Path(argv[1])
    else:
        placed = [m.vendored for m in manifests(root) if m.name == "webex" and m.placed]
        if not placed:
            print("skipped: the SDK file is not placed (ADR 0123 point 3), nothing to scan")
            return 0
        path = placed[0]
    findings = scan(path.read_text(encoding="utf-8", errors="replace"))
    print(report(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
