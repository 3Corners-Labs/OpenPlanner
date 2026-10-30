"""The sender domain check: what SPF, DKIM and DMARC records say about a domain (ADR 0073).

Pure rules over record texts. The lookups go through the ``MailDomainResolver`` port; the
verdicts are one word each (``Verdict``) with a reason code the portal explains in the admin's
language. A lookup that fails is *unknown*, never a guess. The rules follow RFC 7208 (SPF),
RFC 6376 (DKIM) and RFC 7489 (DMARC) as far as a single TXT lookup can: SPF's ``include`` and
``redirect`` are not followed, and nothing here proves that the relay signs with this domain.

    SPF    TXT of the domain              -all or ~all ok; +all or ?all warning
    DKIM   TXT of <selector>._domainkey.  a key ok; empty key revoked; no selector: not checked
    DMARC  TXT of _dmarc.                 p=quarantine or p=reject ok; p=none warning
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from email.utils import parseaddr
from enum import StrEnum

from app.domain.ports import MailDomainResolver


class Record(StrEnum):
    """The three records every receiver evaluates, in the order the report lists them."""

    SPF = "spf"
    DKIM = "dkim"
    DMARC = "dmarc"


class Verdict(StrEnum):
    """What a record check found; ``SEVERITY`` orders them for the overall finding."""

    OK = "ok"
    NOT_CHECKED = "not_checked"
    WARNING = "warning"
    UNKNOWN = "unknown"
    MISSING = "missing"
    INVALID = "invalid"


SEVERITY = tuple(Verdict)
"""From harmless to harmful (a failed lookup weighs more than a warning: nothing was verified);
the report's finding is the worst verdict of its checks."""


REASONS = frozenset(
    {
        "ok",
        "missing",
        "multiple",
        "invalid",
        "unknown",
        "spf_pass_all",
        "spf_neutral_all",
        "dkim_no_selector",
        "dkim_revoked",
        "dmarc_policy_none",
    }
)
"""Every reason code a check can carry; the portal has an explanation for each."""


@dataclass(frozen=True)
class RecordCheck:
    """One record's verdict with its reason code (an i18n key suffix, never free text)."""

    record: Record
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class DomainReport:
    """The verdicts of one domain; the records themselves are not kept."""

    domain: str
    checks: tuple[RecordCheck, ...]

    @property
    def finding(self) -> Verdict:
        """The worst verdict among the checks."""
        return max((c.verdict for c in self.checks), key=SEVERITY.index)


_SPF_VERSION = re.compile(r"^v=spf1(\s|$)", re.IGNORECASE)
_SPF_TERM = re.compile(
    r"^[+\-~?]?(all|include:\S+|a(:\S+)?(/\d+)?|mx(:\S+)?(/\d+)?|ip4:\S+|ip6:\S+|exists:\S+"
    r"|ptr(:\S+)?)$|^[a-z][a-z0-9_.-]*=\S*$",
    re.IGNORECASE,
)
"""A mechanism with an optional qualifier, or a modifier (``redirect=``, ``exp=``, unknown)."""
_SPF_ALL = re.compile(r"^([+\-~?]?)all$", re.IGNORECASE)
_DMARC_VERSION = re.compile(r"^v=dmarc1\s*;", re.IGNORECASE)
_TAG = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*=\s*(.*?)\s*$", re.IGNORECASE)


def _tags(record: str) -> dict[str, str] | None:
    """Parse ``tag=value; tag=value`` (DKIM and DMARC); ``None`` when a part is not a tag."""
    tags: dict[str, str] = {}
    for part in record.split(";"):
        if not part.strip():
            continue
        match = _TAG.match(part)
        if match is None:
            return None
        tags.setdefault(match.group(1).lower(), match.group(2))
    return tags


def assess_spf(records: Sequence[str] | None) -> RecordCheck:
    """Judge the SPF record among the domain's TXT records."""
    if records is None:
        return RecordCheck(Record.SPF, Verdict.UNKNOWN, "unknown")
    spf = [r for r in records if _SPF_VERSION.match(r)]
    if not spf:
        return RecordCheck(Record.SPF, Verdict.MISSING, "missing")
    if len(spf) > 1:
        return RecordCheck(Record.SPF, Verdict.INVALID, "multiple")
    terms = spf[0].split()[1:]
    if any(_SPF_TERM.match(term) is None for term in terms):
        return RecordCheck(Record.SPF, Verdict.INVALID, "invalid")
    qualifier = next((m.group(1) for t in terms if (m := _SPF_ALL.match(t))), None)
    if qualifier in ("-", "~") or any(t.lower().startswith("redirect=") for t in terms):
        return RecordCheck(Record.SPF, Verdict.OK, "ok")
    if qualifier in ("+", ""):
        return RecordCheck(Record.SPF, Verdict.WARNING, "spf_pass_all")
    return RecordCheck(Record.SPF, Verdict.WARNING, "spf_neutral_all")


def assess_dkim(records: Sequence[str] | None, selector: str | None) -> RecordCheck:
    """Judge the DKIM key record at ``<selector>._domainkey.<domain>``."""
    if selector is None:
        return RecordCheck(Record.DKIM, Verdict.NOT_CHECKED, "dkim_no_selector")
    if records is None:
        return RecordCheck(Record.DKIM, Verdict.UNKNOWN, "unknown")
    parsed = [tags for r in records if (tags := _tags(r)) is not None]
    keys = [t for t in parsed if "p" in t or t.get("v", "").upper() == "DKIM1"]
    if not keys:
        return RecordCheck(Record.DKIM, Verdict.MISSING, "missing")
    if len(keys) > 1:
        return RecordCheck(Record.DKIM, Verdict.INVALID, "multiple")
    key = keys[0]
    if "v" in key and key["v"].upper() != "DKIM1" or "p" not in key:
        return RecordCheck(Record.DKIM, Verdict.INVALID, "invalid")
    if not key["p"]:
        return RecordCheck(Record.DKIM, Verdict.INVALID, "dkim_revoked")
    return RecordCheck(Record.DKIM, Verdict.OK, "ok")


def assess_dmarc(records: Sequence[str] | None) -> RecordCheck:
    """Judge the DMARC policy record at ``_dmarc.<domain>``."""
    if records is None:
        return RecordCheck(Record.DMARC, Verdict.UNKNOWN, "unknown")
    dmarc = [r for r in records if _DMARC_VERSION.match(r)]
    if not dmarc:
        return RecordCheck(Record.DMARC, Verdict.MISSING, "missing")
    if len(dmarc) > 1:
        return RecordCheck(Record.DMARC, Verdict.INVALID, "multiple")
    tags = _tags(dmarc[0])
    policy = (tags or {}).get("p", "").lower()
    if policy in ("quarantine", "reject"):
        return RecordCheck(Record.DMARC, Verdict.OK, "ok")
    if policy == "none":
        return RecordCheck(Record.DMARC, Verdict.WARNING, "dmarc_policy_none")
    return RecordCheck(Record.DMARC, Verdict.INVALID, "invalid")


def sender_domain(address: str) -> str:
    """Return the domain of a sender (``Name <user@Demo.NL>`` → ``demo.nl``)."""
    _, bare = parseaddr(address)
    return bare.rpartition("@")[2].lower().rstrip(".")


async def check_domain(
    resolver: MailDomainResolver, domain: str, selector: str | None
) -> DomainReport:
    """Look up the three records of ``domain`` and judge each."""
    spf = assess_spf(await resolver.txt_records(domain))
    dkim = assess_dkim(
        await resolver.txt_records(f"{selector}._domainkey.{domain}") if selector else None,
        selector,
    )
    dmarc = assess_dmarc(await resolver.txt_records(f"_dmarc.{domain}"))
    return DomainReport(domain, (spf, dkim, dmarc))
