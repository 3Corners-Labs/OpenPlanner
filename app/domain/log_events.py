"""The register of every action the application logs or audits (ADR 0103 point 1).

An action is the key of a log line (``storage.degraded``), of an audit, system or security
event (``admin.login``, ``job.send_reminder``) or of a trace point (``http.request``). Each is
registered here once with its category, its level, the fields it carries, an English message
template, what it means and what to do about it. ``docs/logging.md`` is generated from this
register (``python -m app.cli logging-doc``), and the tests fail when

- a log call, a trace point or an audited action in ``app/`` is not registered here,
- a log call passes a field its entry does not declare, or logs at a level it does not list,
- a message template names a field the entry does not declare.

    log.warning("storage.degraded", grade=..)  ─┐
    AuditEvent(.., action="admin.login", ..)    ├─► lookup(action) ─► LogEvent
    tracer.trace("http.request", ..)           ─┘

Three families are generated instead of listed: an audit event whose action is a
``Permission`` (an admin used that right; the matrix in ``docs/authorization.md`` says what it
covers), one whose action is an ``ApiScope`` (a machine API client used that scope), and the
system event of every worker job (``job.<type>``).

The register is code, not a setting (ADR 0103, *Fixed on purpose*): a level or a message an
admin could change would make the log unreliable as evidence, and an editable template could
pull in a field that carries personal data.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from string import Formatter
from typing import Any

from app.domain.authorization import ApiScope, Permission
from app.domain.job_plan import JobType


class LogCategory(StrEnum):
    """The kind of an action; becomes ``event.category`` and the syslog ``MSGID`` (ADR 0103)."""

    AUDIT = "audit"
    """An admin action: a login, a change, an export."""
    SYSTEM = "system"
    """The platform's own work: jobs, checks, housekeeping with a row in the system log."""
    SECURITY = "security"
    """A refusal or failure that may be an attack or a broken trust relation."""
    AUTHZ = "authz"
    """An authorisation decision (CLAUDE.md: every decision is logged)."""
    OPERATIONS = "operations"
    """The running of the processes: start, stop, housekeeping, adapters, trace points."""
    DIAGNOSTIC = "diagnostic"
    """Debug detail while diagnostic mode is on (ADR 0040 part 4); stays in the installation."""


LEVELS = ("trace", "debug", "info", "warning", "error", "critical")
"""The levels a log line can have, lowest first (ADR 0040, ADR 0085 part 2)."""

BY_OUTCOME = "outcome"
"""Level of an audit, system or security event: it follows the outcome (``LEVEL_BY_OUTCOME``)."""

COMMON_FIELDS = (
    "action",
    "outcome",
    "reason",
    "organisation",
    "group",
    "actor",
    "booking_id",
    "detail",
)
"""Fields every message template may name besides the entry's own: the event's envelope."""

NOTHING = "Nothing."
"""What to do about an action that needs no one."""


@dataclass(frozen=True)
class LogEvent:
    """One registered action.

    Attributes:
        action: The key, e.g. ``storage.degraded``.
        category: Its kind.
        levels: The levels it is written at, from ``LEVELS``, or ``BY_OUTCOME`` for an event
            whose level follows its outcome; an action that is both a log line and an event
            lists both.
        fields: The fields of the line, beyond ``COMMON_FIELDS``; a log call may pass no other.
            For a trace point they are the only fields it may carry.
        message: English sentence with ``{placeholders}`` from ``fields`` and ``COMMON_FIELDS``.
        meaning: What happened, for the reader of the log.
        todo: What a person should do, or ``NOTHING``.
        never: For a trace point: what it must never carry.
    """

    action: str
    category: LogCategory
    levels: tuple[str, ...]
    fields: tuple[str, ...]
    message: str
    meaning: str
    todo: str = NOTHING
    never: str = ""

    @property
    def placeholders(self) -> frozenset[str]:
        """The names the message template uses."""
        return frozenset(name for _, name, _, _ in Formatter().parse(self.message) if name)

    @property
    def is_trace_point(self) -> bool:
        """Whether the action is a trace point (written at ``trace``)."""
        return "trace" in self.levels


def _e(
    action: str,
    category: LogCategory,
    levels: str,
    fields: str,
    message: str,
    meaning: str,
    todo: str = NOTHING,
    never: str = "",
) -> LogEvent:
    """Build an entry; ``levels`` and ``fields`` are space-separated for a compact table."""
    return LogEvent(
        action,
        category,
        tuple(levels.split()),
        tuple(fields.split()),
        message,
        meaning,
        todo,
        never,
    )


_AUD, _SYS, _SEC, _DECISION, _OPS, _DIAG = (
    LogCategory.AUDIT,
    LogCategory.SYSTEM,
    LogCategory.SECURITY,
    LogCategory.AUTHZ,
    LogCategory.OPERATIONS,
    LogCategory.DIAGNOSTIC,
)

_TRACE_POINTS = (
    _e(
        "http.request",
        _OPS,
        "trace",
        "method route status ms",
        "{method} {route} answered {status} in {ms} ms.",
        "One HTTP request, from `app/api/trace.py`; `route` is the matched route template "
        "(`/g/{slug}`), empty for a path that matched no route.",
        never="the raw path (`/b/{token}` carries the management token), the query, the "
        "client address",
    ),
    _e(
        "job.run",
        _DIAG,
        "trace debug",
        "type attempt ms",
        "Job {type} ran (attempt {attempt}) in {ms} ms.",
        "One job the worker ran (`app/jobs/runner.py`); while diagnostic mode is on for its "
        "group it is also a diagnostic event with the same values in `detail`.",
        never="the booking's data",
    ),
    _e(
        "job.step",
        _OPS,
        "trace",
        "type step ms",
        "Job {type} finished step {step} after {ms} ms.",
        "A step of a message job: `load`, `compose`, `send`.",
        never="the recipient, the message",
    ),
    _e(
        "outbound.request",
        _OPS,
        "trace",
        "method host status ms error",
        "{method} to {host} answered {status} in {ms} ms.",
        "One outgoing HTTP call of web or worker (`app/adapters/http_trace.py`); `error` is the "
        "exception type when no response came.",
        never="the URL, a query, a header, a body",
    ),
    _e(
        "sql.slow",
        _OPS,
        "trace",
        "statement table ms",
        "A {statement} on {table} took {ms} ms.",
        "A statement slower than `SQL_SLOW_MS` (`app/adapters/db/engine.py`); `statement` is the "
        "kind (`Select`, `Insert`, `raw`), `table` the first table.",
        "Look for a missing index when the same table keeps coming back.",
        never="the statement text, parameters",
    ),
    _e(
        "template.render",
        _OPS,
        "trace",
        "template ms",
        "Page {template} rendered in {ms} ms.",
        "Every page rendered through `app.web.templates.render`.",
        never="the context",
    ),
)

_FAILED = "The step is skipped this round and tried again at the next; nothing else stops."
_CHECK_HOST = (
    "Look at the container log around it; repeated failures point at the host or the database."
)

_OPERATIONS = (
    _e(
        "address.probe_failed",
        _OPS,
        "warning",
        "error",
        "The public address probe failed: {error}.",
        "The round trip of *System status* (ADR 0086) could not reach this installation "
        "through its public address.",
        "Check `PUBLIC_BASE_URL`, DNS and the proxy; *System status* shows the finding.",
    ),
    _e(
        "address.probe_trust_failed",
        _OPS,
        "warning",
        "error",
        "The public address probe could not load its trusted root: {error}.",
        "The proxy's published internal root (ADR 0112) could not be read or parsed, so the "
        "round trip of *System status* (ADR 0086) answered `tls` instead of checking the address.",
        "Check the volume `internal-ca` and its file `root.crt`; the proxy writes it.",
    ),
    _e(
        "address.proxy_event_failed",
        _OPS,
        "warning",
        "error",
        "The proxy event could not be written: {error}.",
        "A request came through an unexpected proxy hop and the system event about it failed "
        "to save; the request itself went on.",
        _CHECK_HOST,
    ),
    _e(
        "address.round_trip",
        _OPS,
        "warning",
        "finding kind",
        "The public address check found {finding} ({kind}).",
        "The worker's round trip through the public address (ADR 0086) did not come back as "
        "expected.",
        "Open *System status*: it names the finding and the hop that answered.",
    ),
    _e(
        "address.watch_failed",
        _OPS,
        "warning",
        "error",
        "The public address check did not run: {error}.",
        "The worker could not run the address round trip; often the schema is not migrated yet.",
        _CHECK_HOST,
    ),
    _e(
        "audit.emit_failed",
        _OPS,
        "error",
        "error",
        "An audit event could not be written to the log stream: {error}.",
        "The event is in the audit table, but its copy on stdout (and so on the log platform) "
        "failed.",
        "Compare the audit log in the portal with the log platform for the period.",
    ),
    _e(
        "backups.prune_failed",
        _OPS,
        "warning",
        "error",
        "Old back-ups could not be removed: {error}.",
        "Housekeeping could not prune `data/backups`.",
        "Check the data volume's permissions and free space.",
    ),
    _e(
        "backups.pruned",
        _OPS,
        "info",
        "backups",
        "{backups} old back-ups removed.",
        "Housekeeping removed back-ups older than the retention.",
    ),
    _e(
        "usage.rollup_failed",
        _OPS,
        "error",
        "error",
        "The usage facts could not be rolled up: {error}.",
        "The rollup of ADR 0117 failed this round; the facts wait and are tried again at the "
        "next interval. System status shows how far behind it is.",
        _CHECK_HOST,
    ),
    _e(
        "usage.rolled_up",
        _OPS,
        "info",
        "facts cells unreadable events_pruned hours_pruned",
        "{facts} usage facts rolled up into {cells} hourly cells.",
        "The worker added usage facts to the hourly table and removed what retention allows "
        "(ADR 0117).",
    ),
    _e(
        "usage.fact_dropped",
        _OPS,
        "warning",
        "kind",
        "A usage fact of kind {kind} could not be written.",
        "A booking change committed but its fact for the reports was dropped (ADR 0117); "
        "reporting never fails a participant's action.",
        _CHECK_HOST,
    ),
    _e(
        "booking_rows.rollup_failed",
        _OPS,
        "error",
        "error",
        "Old booking rows could not be merged into daily counts: {error}.",
        "The rollup of ADR 0102 failed this round; the rows stay until the next.",
        _CHECK_HOST,
    ),
    _e(
        "database.reclaim_failed",
        _OPS,
        "warning",
        "error",
        "Free database pages could not be given back: {error}.",
        "The incremental vacuum failed; the file keeps its size until the next round.",
        _FAILED,
    ),
    _e(
        "database.reclaimed",
        _OPS,
        "info",
        "pages",
        "{pages} free database pages given back to the disk.",
        "The incremental vacuum shrank the database file (ADR 0040 point 2).",
    ),
    _e(
        "database.unreachable",
        _OPS,
        "warning",
        "error",
        "The database did not answer: {error}.",
        "The readiness check could not reach SQLite.",
        "Check the data volume and the disk; `/readyz` answers 503 meanwhile.",
    ),
    _e(
        "diagnostics.brake_failed",
        _OPS,
        "warning",
        "error",
        "Diagnostic mode could not be switched off for a full disk: {error}.",
        "The emergency brake of ADR 0040 point 13 failed, probably because the disk is full.",
        "Free disk space on the host at once.",
    ),
    _e(
        "diagnostics.braked",
        _OPS,
        "warning",
        "modes",
        "Diagnostic mode switched off in {modes} places because the disk is nearly full.",
        "The emergency brake of ADR 0040 point 13 ended diagnostic mode.",
        "Free disk space; switch diagnostic mode on again when needed.",
    ),
    _e(
        "diagnostics.failed",
        _OPS,
        "warning",
        "error job_id",
        "The diagnostic event of job {job_id} could not be written: {error}.",
        "Debug detail of a job was lost; the job itself went on.",
    ),
    _e(
        "direct.deferred",
        _OPS,
        "warning",
        "booking_id error",
        "The call for booking {booking_id} is left to the worker: {error}.",
        "A start-now call could not be set up during the request; the worker's jobs retry it.",
        "Watch the booking's timeline; repeated failures show the provider's reason there.",
    ),
    _e(
        "direct.left",
        _OPS,
        "info",
        "agent_present booking_id meeting_id",
        "The participant of booking {booking_id} left the queue.",
        "A participant left a start-now call before or after it began.",
    ),
    _e(
        "dns.lookup_failed",
        _OPS,
        "warning",
        "domain error",
        "DNS lookup for {domain} failed: {error}.",
        "The email deliverability check (ADR 0073) could not resolve a record.",
        "Check the host's DNS resolver; the check on *Channels* stays incomplete meanwhile.",
    ),
    _e(
        "group.erase_done",
        _OPS,
        "info",
        "groups",
        "{groups} deleted groups erased.",
        "Housekeeping erased groups whose deletion period ended.",
    ),
    _e(
        "group.erase_failed",
        _OPS,
        "warning",
        "error",
        "Deleted groups could not be erased: {error}.",
        "The erasure of deleted groups failed this round.",
        _FAILED,
    ),
    _e(
        "i18n.missing_key",
        _OPS,
        "warning",
        "key locale",
        "Text {key} is missing in {locale}.",
        "A page asked for a text that has no translation; the key is shown instead (once per "
        "process and locale).",
        "Report it: a catalogue entry is missing.",
    ),
    _e(
        "i18n.overrides_refresh_failed",
        _OPS,
        "warning",
        "error",
        "Organisation texts could not be reloaded: {error}.",
        "The pages keep the texts they had; a change in the portal shows later.",
        _FAILED,
    ),
    _e(
        "instance.prepare_failed",
        _OPS,
        "warning",
        "error",
        "The instance identity could not be prepared: {error}.",
        "At start-up the instance key for the address round trip was not ready; often the "
        "schema is not migrated yet.",
        _CHECK_HOST,
    ),
    _e(
        "integration.check_failed",
        _OPS,
        "warning",
        "error group",
        "The integration check did not run: {error}.",
        "The scheduled or manual check of a group's Webex and Contact Center connection crashed.",
        _CHECK_HOST,
    ),
    _e(
        "integration.checked",
        _OPS,
        "info",
        "groups",
        "Integrations of {groups} groups checked.",
        "The worker's scheduled integration check ran.",
    ),
    _e(
        "integration.refused",
        _OPS,
        "warning",
        "path reason status tracking_id",
        "The provider refused {path} with {status}: {reason}.",
        "Webex answered a request with an error; `tracking_id` is Webex's own reference.",
        "Look up `tracking_id` with Webex support when the reason does not explain it.",
    ),
    _e(
        "integration.unreachable",
        _OPS,
        "warning",
        "error",
        "A provider could not be reached: {error}.",
        "An outgoing call to Webex, the SMS gateway or the release feed got no answer.",
        "Check the host's outbound network and the provider's status page.",
    ),
    _e(
        "job.checkpoint_failed",
        _OPS,
        "warning",
        "error",
        "The WAL checkpoint after a purge failed: {error}.",
        "The purged data is gone from the database, but old pages stay in the WAL file until "
        "the next checkpoint.",
        _FAILED,
    ),
    _e(
        "job.failed",
        _OPS,
        "error",
        "error job_id type",
        "Job {job_id} ({type}) failed for good: {error}.",
        "A job ran out of attempts or failed in a way that does not retry.",
        "Open the booking's timeline; the system log has the provider's reason.",
    ),
    _e(
        "job.lapsed",
        _OPS,
        "info",
        "booking_id job_id",
        "Booking {booking_id} lapsed (job {job_id}).",
        "A booking that waited for a new time or a confirmation passed its deadline.",
    ),
    _e(
        "job.purged",
        _OPS,
        "info",
        "booking_id job_id",
        "Personal data of booking {booking_id} deleted (job {job_id}).",
        "The retention job deleted the personal fields of a booking (ADR 0102).",
    ),
    _e(
        "job.retry",
        _OPS,
        "warning",
        "attempt error job_id",
        "Job {job_id} failed (attempt {attempt}) and will be retried: {error}.",
        "A job failed with a retryable error and is planned again with backoff.",
        "Nothing, unless it ends in `job.failed`.",
    ),
    _e(
        "job.sent",
        _OPS,
        "info",
        "channel job_id type",
        "Job {job_id} ({type}) sent its message by {channel}.",
        "A message job handed its message to the email or SMS provider.",
    ),
    _e(
        "job.skipped",
        _OPS,
        "info",
        "job_id reason",
        "Job {job_id} skipped: {reason}.",
        "A job found nothing to do: the booking moved, was cancelled, or has no recipient.",
    ),
    _e(
        "logging.test",
        _OPS,
        "info",
        "destination kind",
        "Test line for log destination {destination} ({kind}).",
        "*Send test line* on a log destination (ADR 0085 point 5); it reaches only that "
        "destination.",
    ),
    _e(
        "queue.wait_watch_failed",
        _OPS,
        "warning",
        "error",
        "The queue wait check did not run: {error}.",
        "The worker could not compare the own queues' waits with their threshold (ADR 0061 OV-7).",
        _CHECK_HOST,
    ),
    _e(
        "queue.wait_crossed",
        _OPS,
        "info",
        "groups",
        "{groups} own queues crossed their wait threshold.",
        "The longest wait of these queues went above or back under `queue_wait_warning_minutes`; "
        "each crossing is a `queue.wait` system event.",
        "None; the alert is on the overview.",
    ),
    _e(
        "alerts.read_failed",
        _OPS,
        "warning",
        "error",
        "The open alerts could not be read: {error}.",
        "An admin page showed without its alert banner, often because the database is full or "
        "away (ADR 0061 §7).",
        _CHECK_HOST,
    ),
    _e(
        "maintenance.watch_failed",
        _OPS,
        "warning",
        "error",
        "The maintenance check did not run: {error}.",
        "The worker could not check whether maintenance mode is overdue.",
        _CHECK_HOST,
    ),
    _e(
        "request_log.failed",
        _OPS,
        "warning",
        "error",
        "A provider request could not be recorded: {error}.",
        "The request chain of ADR 0065 lost one entry; the request itself went on.",
    ),
    _e(
        "retention.backlog",
        _OPS,
        "error",
        "bookings failed",
        "{bookings} bookings still hold personal data past their retention ({failed} failed).",
        "The nightly sweep found bookings whose personal data should have been deleted 48 hours "
        "ago.",
        "Open *Retention* in the portal and the worker's log; this is a privacy incident when it "
        "persists.",
    ),
    _e(
        "retention.checkpoint_failed",
        _OPS,
        "warning",
        "error",
        "The WAL checkpoint after the retention sweep failed: {error}.",
        "The deleted data is gone from the database; old pages stay in the WAL until the next "
        "checkpoint.",
        _FAILED,
    ),
    _e(
        "retention.closure_notice",
        _OPS,
        "warning",
        "closures",
        "{closures} closures near their maximum retention.",
        "Open-ended closures are close to `closure_max_retention_days` (ADR 0102).",
        "End or shorten the closures; their bookings are purged at the maximum.",
    ),
    _e(
        "retention.sweep_failed",
        _OPS,
        "error",
        "error",
        "The retention sweep failed: {error}.",
        "The nightly check for personal data past its retention did not run.",
        "Look at the worker's log at once; the sweep is the safety net of the purge.",
    ),
    _e(
        "security_log.failed",
        _OPS,
        "warning",
        "error",
        "A security event could not be written: {error}.",
        "A refusal or failure was not saved to the security log; it is on stdout instead.",
        _CHECK_HOST,
    ),
    _e(
        "sessions.clean_failed",
        _OPS,
        "warning",
        "error",
        "Expired admin sessions could not be removed: {error}.",
        "Housekeeping could not delete expired sessions; they stay unusable.",
        _FAILED,
    ),
    _e(
        "sessions.cleaned",
        _OPS,
        "info",
        "sessions",
        "{sessions} expired admin sessions removed.",
        "Housekeeping deleted admin sessions past their lifetime.",
    ),
    _e(
        "settings.portal_values_refused",
        _OPS,
        "warning",
        "version",
        "Portal settings version {version} refused; the previous values stay.",
        "A combination of portal settings broke a rule of ADR 0042 and was not applied.",
        "Open *Advanced settings*: the refused combination is shown there.",
    ),
    _e(
        "settings.reload_failed",
        _OPS,
        "warning",
        "error",
        "Portal settings could not be reloaded: {error}.",
        "The process keeps the settings it had.",
        _FAILED,
    ),
    _e(
        "settings_key.missing",
        _OPS,
        "warning",
        "",
        "The settings encryption key is missing.",
        "An integration secret was asked for, but `SETTINGS_ENCRYPTION_KEY` is not set.",
        "Set the key on the host (runbook, *Rotating secrets*).",
    ),
    _e(
        "sms.accepted",
        _OPS,
        "info",
        "correlation_id",
        "The SMS gateway accepted a message ({correlation_id}).",
        "Webex Connect took the message; `correlation_id` is its reference.",
    ),
    _e(
        "staff.erase_failed",
        _OPS,
        "warning",
        "error",
        "Staff past their retention could not be erased: {error}.",
        "Housekeeping could not erase suspended staff (ADR 0046).",
        _FAILED,
    ),
    _e(
        "routing.entry_skipped",
        _OPS,
        "warning",
        "part count",
        "Skipped {count} stored routing {part} that no longer validate.",
        "A group's stored subjects, attributes or mapping (ADR 0081) hold an entry this release "
        "does not accept, for example an attribute a newer release added; the rest is used. The "
        "next change on the group's *Subjects and routing* screen saves the routing without it.",
        "Check the group's *Subjects and routing* screen; after a rollback, move forward again "
        "before changing it.",
        never="the labels, the values",
    ),
    _e(
        "storage.degraded",
        _OPS,
        "warning error",
        "database_mb free_percent grade reason",
        "Disk space is {grade}: {free_percent}% free, database {database_mb} MB ({reason}).",
        "The worker measured the data volume below a limit of ADR 0040 point 3; logged on change "
        "and then hourly.",
        "Free disk space on the host (old images: `docker image prune`); at `critical` "
        "diagnostic mode stops.",
    ),
    _e(
        "storage.record_failed",
        _OPS,
        "warning",
        "error",
        "The storage check could not be saved: {error}.",
        "The system event about the disk failed, often because the disk is full; this line is "
        "the fallback.",
        "Free disk space on the host.",
    ),
    _e(
        "storage.recovered",
        _OPS,
        "info",
        "",
        "Disk space is back within its limits.",
        "The data volume is healthy again after a `storage.degraded`.",
    ),
    _e(
        "system_log.bind_failed",
        _OPS,
        "warning",
        "error job_id",
        "Job {job_id} could not be tagged with its group: {error}.",
        "The job's log lines lack organisation and group; the job itself went on.",
    ),
    _e(
        "system_log.failed",
        _OPS,
        "warning",
        "error job_id",
        "The system event of job {job_id} could not be written: {error}.",
        "The job's outcome is settled, but the booking's timeline misses it.",
        _CHECK_HOST,
    ),
    _e(
        "system_log.prune_failed",
        _OPS,
        "warning",
        "error",
        "Old system events could not be removed: {error}.",
        "Pruning of the system log failed this round.",
        _FAILED,
    ),
    _e(
        "system_log.pruned",
        _OPS,
        "info",
        "removed",
        "{removed} old system events removed.",
        "The system log was pruned to its retention (ADR 0040 point 12).",
    ),
    _e(
        "tls.handshake_failed",
        _OPS,
        "warning",
        "error host",
        "The TLS handshake with {host} failed: {error}.",
        "The certificate check could not complete a handshake.",
        "Check the proxy's certificate and protocols; *System status* shows the finding.",
    ),
    _e(
        "tls.unreachable",
        _OPS,
        "warning",
        "error host",
        "{host} could not be reached for the certificate check: {error}.",
        "The certificate check got no connection.",
        "Check the address and the proxy.",
    ),
    _e(
        "tls.volume.key_imported",
        _OPS,
        "info",
        "",
        "An imported private key was placed on the certificate volume.",
        "The key of an uploaded certificate is now on the host volume (ADR 0070).",
    ),
    _e(
        "tls.volume.key_promoted",
        _OPS,
        "info",
        "",
        "The key of the signed request was promoted for use.",
        "The certificate for the pending request arrived; its key becomes the serving key.",
    ),
    _e(
        "tls.volume.key_unreadable",
        _OPS,
        "warning",
        "file",
        "Key file {file} on the certificate volume cannot be read.",
        "A key file is damaged or not a key.",
        "Replace the certificate through the portal (*Certificate*).",
    ),
    _e(
        "tls.volume.no_key_to_serve_with",
        _OPS,
        "warning",
        "",
        "A certificate chain is ready but there is no key to serve it with.",
        "The proxy cannot use the new chain without its private key.",
        "Create a new request or upload the certificate with its key.",
    ),
    _e(
        "tls.volume.publish_failed",
        _OPS,
        "warning",
        "error",
        "The certificate could not be published to the proxy: {error}.",
        "Writing the chain to the certificate volume failed.",
        "Check the volume's permissions and free space.",
    ),
    _e(
        "tls.volume.published",
        _OPS,
        "info",
        "fingerprint",
        "Certificate {fingerprint} published to the proxy.",
        "The proxy picks up the new chain.",
    ),
    _e(
        "tls.volume.request_made",
        _OPS,
        "info",
        "",
        "A certificate signing request was written to the volume.",
        "A new key and CSR are waiting for a signed certificate (ADR 0070).",
    ),
    _e(
        "tls.volume.unreadable",
        _OPS,
        "warning",
        "error",
        "The certificate volume cannot be read: {error}.",
        "The pending request or the published chain could not be read.",
        "Check that the volume is mounted with the right owner.",
    ),
    _e(
        "tls.volume.unwritable",
        _OPS,
        "warning",
        "error",
        "The certificate volume cannot be written: {error}.",
        "A certificate change was refused because the volume is read-only or full.",
        "Check that the volume is mounted writable with the right owner.",
    ),
    _e(
        "tls.volume.withdraw_failed",
        _OPS,
        "warning",
        "error",
        "The certificate could not be withdrawn from the proxy: {error}.",
        "Removing the chain from the certificate volume failed.",
        "Check the volume's permissions.",
    ),
    _e(
        "tls.volume.withdrawn",
        _OPS,
        "info",
        "",
        "The certificate was withdrawn from the proxy.",
        "The proxy falls back to its default certificate.",
    ),
    _e(
        "integration.health_watch_failed",
        _OPS,
        "warning",
        "error",
        "The reconcile and health sweep did not run: {error}.",
        "The worker could not reconcile stale tasks or recompute the connection health (ADR 0082).",
        _CHECK_HOST,
    ),
    _e(
        "idp.domain_watch_failed",
        _OPS,
        "warning",
        "error",
        "The re-check of the login domains did not run: {error}.",
        "The worker could not run the scheduled TXT check of the login domains (ADR 0083).",
        _CHECK_HOST,
    ),
    _e(
        "tls.watch_failed",
        _OPS,
        "warning",
        "error",
        "The certificate check did not run: {error}.",
        "The worker could not run the scheduled certificate check.",
        _CHECK_HOST,
    ),
    _e(
        "updates.check_failed",
        _OPS,
        "warning",
        "error",
        "The release check did not run: {error}.",
        "The worker could not look up the latest release.",
        "Check the outbound network to GitHub.",
    ),
    _e(
        "updates.checked",
        _OPS,
        "info",
        "current",
        "Release check done; running {current}.",
        "The worker looked up the latest release of its channel (ADR 0059).",
    ),
    _e(
        "updates.release_ignored",
        _OPS,
        "warning",
        "reason",
        "A release in the feed was ignored: {reason}.",
        "The release feed held an entry that is not a version or points elsewhere.",
    ),
    _e(
        "updates.support_ignored",
        _OPS,
        "warning",
        "reason",
        "The support index was ignored: {reason}.",
        "support.json on the repository's default branch does not parse (ADR 0116 point 9); "
        "the updates page keeps the last good one.",
    ),
    _e(
        "updates.version_note_failed",
        _OPS,
        "warning",
        "error",
        "The running version could not be noted: {error}.",
        "At start-up the version change was not written to the audit log.",
        _CHECK_HOST,
    ),
    _e(
        "webex.refresh_token_expiring",
        _OPS,
        "warning",
        "",
        "The Webex refresh token expires soon.",
        "The service app's refresh token is close to its end of life.",
        "Authorise the Webex service app again under *Integrations*.",
    ),
    _e(
        "webex.token_refresh_failed",
        _OPS,
        "warning",
        "error status",
        "Refreshing the Webex token failed ({status}): {error}.",
        "Webex could not be reached or answered an error for a token refresh; it is retried.",
        "Check the outbound network; see also the security event `webex.token_refresh`.",
    ),
    _e(
        "webex.token_refresh_refused",
        _OPS,
        "error",
        "status",
        "Webex refused the token refresh ({status}).",
        "The refresh token is no longer valid; calls to Webex fail until it is replaced.",
        "Authorise the Webex service app again under *Integrations*.",
    ),
    _e(
        "webhook.webex",
        _OPS,
        "info",
        "kind meeting_id outcome",
        "Webex reported {kind} for meeting {meeting_id}: {outcome}.",
        "A verified webhook of Webex Meetings was processed.",
    ),
    _e(
        "webhook.note_failed",
        _OPS,
        "warning",
        "source error",
        "The last {source} webhook could not be noted: {error}.",
        "A verified webhook was processed, but storing it as the platform's latest inbound "
        "event failed (ADR 0082 point 5); the event itself is recorded.",
        _CHECK_HOST,
    ),
    _e(
        "webhook.wxcc",
        _OPS,
        "info",
        "kind outcome",
        "Webex Contact Center reported {kind}: {outcome}.",
        "A verified webhook of Webex Contact Center was processed.",
    ),
    _e(
        "widget.invalid_request",
        _OPS,
        "info",
        "fields",
        "The widget sent an invalid request (fields {fields}).",
        "A booking or availability request did not validate; only the field names are logged.",
    ),
    _e(
        "worker.heartbeat_failed",
        _OPS,
        "warning",
        "error",
        "The worker heartbeat could not be written: {error}.",
        "*System status* may show the worker as stale.",
        _CHECK_HOST,
    ),
    _e(
        "worker.run_failed",
        _OPS,
        "warning",
        "error",
        "The worker loop failed this round: {error}.",
        "Running the due jobs crashed; the loop goes on.",
        _CHECK_HOST,
    ),
    _e(
        "worker.started",
        _OPS,
        "info",
        "dispatch_enabled fallback_mail poll_seconds release_check video_enabled",
        "Worker started; polling every {poll_seconds} s.",
        "The worker process started, with the switches it runs with.",
    ),
    _e(
        "worker.stopped",
        _OPS,
        "info",
        "",
        "Worker stopped.",
        "The worker process ended cleanly.",
    ),
)

_SECURITY = (
    _e(
        "agent_api.invalid_request",
        _SEC,
        "info",
        "fields",
        "The machine API got an invalid request (fields {fields}).",
        "A request to `/api/v1` did not validate; only the field names are logged.",
    ),
    _e(
        "agent_api.refused",
        _SEC,
        "info",
        "reason",
        "A machine API caller was refused: {reason}.",
        "The bearer token was missing, unknown, expired or from a disallowed address.",
        "Repeated refusals from one client: check its credentials, or a guessing attempt.",
    ),
    _e(
        "idp.discovery_failed",
        _SEC,
        "warning",
        "error",
        "The identity provider's discovery document could not be read: {error}.",
        "An organisation's identity provider did not answer its `.well-known` address.",
        "Check the provider's address under the organisation's *Login*.",
    ),
    _e(
        "idp.discovery_refused",
        _SEC,
        "warning",
        "reason",
        "The identity provider's address was refused: {reason}.",
        "The address points to a host the outbound guard does not allow.",
        "Correct the provider's address.",
    ),
    _e(
        "oidc.discovery_failed",
        _SEC,
        "warning",
        "error",
        "The login provider's discovery document could not be read: {error}.",
        "Admin login cannot start until the provider answers.",
        "Run `python -m app.cli oidc-check` on the host.",
    ),
    _e(
        "oidc.invalid_token",
        _SEC,
        "warning",
        "error",
        "An ID token was refused: {error}.",
        "A login returned a token that failed a check (signature, audience, expiry, `azp`).",
        "One-off: nothing. Repeated: check the provider's keys and client configuration.",
    ),
    _e(
        "oidc.issuer_mismatch",
        _SEC,
        "warning",
        "",
        "The login provider's issuer does not match the configured one.",
        "The discovery document names another issuer than `OIDC_ISSUER` (exact match).",
        "Correct `OIDC_ISSUER` or the provider; `python -m app.cli oidc-check` shows both.",
    ),
    _e(
        "oidc.mfa_missing",
        _SEC,
        "warning",
        "provider",
        "A login through {provider} showed no second factor.",
        "The token carried no `acr` or `amr` value that shows MFA (ADR 0100).",
        "Check the provider's MFA policy for the user.",
    ),
    _e(
        "oidc.no_role",
        _SEC,
        "warning",
        "actor claims_present entitlements_seen missing_claim role_values_seen",
        "A login carried no known role (missing claim: {missing_claim}).",
        "The user signed in but no claim value maps to a role; only claim names and counts are "
        "logged.",
        "Compare the claims with the mapping under *Login*; `oidc-check` prints the mapping.",
    ),
    _e(
        "oidc.no_scope",
        _SEC,
        "warning",
        "actor claims_present missing_claim",
        "A login carried a role without its organisation or group (missing claim: "
        "{missing_claim}).",
        "A scoped role needs its scope claim; the login is refused.",
        "Add the scope claim at the identity provider.",
    ),
    _e(
        "oidc.subject_refused",
        _SEC,
        "warning",
        "reason",
        "A login's subject was refused: {reason}.",
        "The `sub` claim is empty, too long or has characters that are not allowed.",
        "Check the identity provider's subject format.",
    ),
    _e(
        "oidc.response_issuer_mismatch",
        _SEC,
        "warning",
        "provider",
        "A login answer named another issuer than provider {provider} (RFC 9207).",
        "The authorisation response's iss did not match, or was missing while the provider "
        "announces it: a mix-up attempt or a misconfigured provider (ADR 0083 point 14).",
        "Check the provider's issuer on its page; repeated events may be an attack.",
    ),
    _e(
        "oidc.token_exchange_failed",
        _SEC,
        "warning",
        "error",
        "The login code could not be exchanged for a token: {error}.",
        "The provider's token endpoint refused or did not answer.",
        "Check the client secret and the provider's status.",
    ),
    _e(
        "oidc.too_many_entitlements",
        _SEC,
        "warning",
        "actor values",
        "A login carried {values} entitlements, more than allowed.",
        "The token had more entitlement values than `oidc_max_entitlements`; the login is refused.",
        "Reduce the user's groups at the provider, or raise the limit.",
    ),
    _e(
        "settings.secret_file_readable",
        _SEC,
        "warning",
        "variable",
        "The secret file of {variable} can be read by others.",
        "A bootstrap secret file (`X_FILE`, ADR 0042 §9) has loose permissions.",
        "Run `chmod 600` on the file.",
    ),
    _e(
        "widget.origin_refused",
        _SEC,
        "info",
        "group origin",
        "The widget of {group} refused origin {origin}.",
        "A site that is not on the group's allow-list tried to use the widget.",
        "Add the site under the group's *Embedding*, or ignore when it is not yours.",
    ),
)

_AUTHZ = (
    _e(
        "agent_api.decision",
        _DECISION,
        "info",
        "allowed client scope",
        "Machine API client {client}: scope {scope}, allowed {allowed}.",
        "The decision on a machine API call (ADR 0023).",
    ),
    _e(
        "authz.decision",
        _DECISION,
        "info",
        "actor allowed permission reason target target_group target_organisation",
        "Permission {permission} on {target}: allowed {allowed} ({reason}).",
        "The decision on an admin request (ADR 0014); a denial is also an audit event.",
        "Nothing; a destination may leave the category `authz` out.",
    ),
    _e(
        "authz.maintenance_refused",
        _DECISION,
        "info",
        "actor permission target target_group target_organisation",
        "Permission {permission} on {target} refused: maintenance mode is on.",
        "A change was refused because maintenance mode is on (ADR 0084).",
    ),
)

_ADMIN_SEEN = "Nothing; the audit log shows who did it."

_AUDIT = (
    _e(
        "admin.login",
        _AUD,
        "info outcome",
        "roles",
        "Admin {actor} signed in.",
        "An admin signed in; the subject is the identity provider's, never a name.",
    ),
    _e(
        "admin.login.mfa_missing",
        _SEC,
        "outcome",
        "",
        "A login was refused: no second factor shown.",
        "The identity provider's token showed no MFA (ADR 0100).",
        "Check the provider's MFA policy for the user.",
    ),
    _e(
        "admin.logout",
        _AUD,
        "outcome",
        "",
        "Admin {actor} signed out.",
        "An admin ended their session.",
    ),
    _e(
        "admin.session_ended",
        _AUD,
        "outcome",
        "",
        "An admin session ended: {reason}.",
        "A session ended by expiry, idle time, the session limit or because its provider "
        "rejected it (ADR 0098, 0083).",
    ),
    _e(
        "admin.sessions_ended_all",
        _AUD,
        "outcome",
        "",
        "All admin sessions were ended.",
        "The superuser or the host ended every session at once.",
        _ADMIN_SEEN,
    ),
    _e(
        "admin.sessions_ended_by_admin",
        _AUD,
        "outcome",
        "",
        "An admin's sessions were ended by another admin.",
        "A staff manager signed someone out everywhere.",
        _ADMIN_SEEN,
    ),
    _e(
        "admin.sessions_ended_by_self",
        _AUD,
        "outcome",
        "",
        "Admin {actor} ended their other sessions.",
        "An admin signed out their other devices.",
    ),
    _e(
        "api.call",
        _AUD,
        "outcome",
        "",
        "Machine API call: {outcome}.",
        "A call to the machine API (ADR 0023).",
    ),
    _e(
        "api.token",
        _AUD,
        "outcome",
        "",
        "Machine API token issued: {outcome}.",
        "A machine API client got an access token.",
    ),
    _e(
        "audit.export",
        _AUD,
        "outcome",
        "",
        "The audit log was exported.",
        "An admin downloaded a CSV of the audit log (ADR 0040 point 10).",
        _ADMIN_SEEN,
    ),
    _e(
        "auth.ambiguous_claims",
        _AUD,
        "outcome",
        "",
        "A login carried claims that map to more than one organisation.",
        "The login is refused because the scope is ambiguous.",
        "Fix the user's claims at the identity provider.",
    ),
    _e(
        "auth.entitlement_mismatch",
        _AUD,
        "outcome",
        "",
        "A login's entitlements do not match its organisation's provider.",
        "An organisation's identity provider sent entitlements for another organisation "
        "(ADR 0083).",
        "Check the entitlement prefix and the provider's mapping.",
    ),
    _e(
        "auth.entitlement_refused",
        _AUD,
        "outcome",
        "",
        "A login's entitlement was refused: {reason}.",
        "An entitlement value did not match the installation's prefix or a known role.",
        "Check the entitlement values at the identity provider.",
    ),
    _e(
        "booking_rows.rollup",
        _SYS,
        "info outcome",
        "days rows skipped",
        "{rows} old booking rows merged into {days} daily counts.",
        "Booking rows past their retention became daily counts and were deleted (ADR 0102).",
    ),
    _e(
        "closure.retention_notice",
        _SYS,
        "outcome",
        "",
        "A closure is near its maximum retention.",
        "An open-ended closure is close to `closure_max_retention_days` (ADR 0102).",
        "End or shorten the closure.",
    ),
    _e(
        "database.error",
        _SEC,
        "outcome",
        "",
        "A request failed on a database error.",
        "A request hit a database error that is not a busy lock; the participant got a 503 "
        "(ASVS V16.3.4).",
        "Look at the container log for the exception type; check the disk.",
    ),
    _e(
        "diagnostics.brake",
        _SYS,
        "outcome",
        "",
        "Diagnostic mode was switched off: the disk is nearly full.",
        "The emergency brake of ADR 0040 point 13.",
        "Free disk space on the host.",
    ),
    _e(
        "group.erased",
        _SYS,
        "outcome",
        "",
        "A deleted group was erased.",
        "A group's data was removed after its deletion period.",
    ),
    _e(
        "group.idp_key_changed",
        _AUD,
        "outcome",
        "",
        "A group's identity provider key changed.",
        "The group's staff now sign in through another provider.",
        _ADMIN_SEEN,
    ),
    _e(
        "idp.created",
        _AUD,
        "outcome",
        "",
        "An identity provider was added.",
        "An organisation registered a login provider (ADR 0083).",
        _ADMIN_SEEN,
    ),
    _e(
        "idp.deleted",
        _AUD,
        "outcome",
        "",
        "An identity provider was deleted.",
        "An organisation removed a login provider.",
        _ADMIN_SEEN,
    ),
    _e(
        "idp.disabled",
        _AUD,
        "outcome",
        "",
        "An identity provider was switched off.",
        "Its users can no longer sign in.",
        _ADMIN_SEEN,
    ),
    _e(
        "idp.enabled",
        _AUD,
        "outcome",
        "",
        "An identity provider was switched on.",
        "Its users can sign in.",
    ),
    _e(
        "idp.domain_check",
        _SYS,
        "outcome",
        "",
        "Re-check of a login domain: {reason}.",
        "The worker looked up the TXT record of a routing domain and its state changed: failing "
        "(still routes), lapsed (stops routing) or verified again (ADR 0083 point 12).",
        "Ask the organisation to restore the TXT record shown on the provider's page.",
    ),
    _e(
        "idp.domain_claimed",
        _AUD,
        "outcome",
        "",
        "An e-mail domain was claimed for an identity provider; it routes once verified.",
        "The superuser added a login domain; the detail names the provider and the domain.",
    ),
    _e(
        "idp.domain_failed",
        _AUD,
        "outcome",
        "",
        "A login domain's TXT record was not found; the domain is failing or lapsed.",
        "After the grace period the domain stops sending logins to the organisation provider.",
        "Ask the organisation to restore the TXT record, or remove the domain.",
    ),
    _e(
        "idp.domain_removed",
        _AUD,
        "outcome",
        "",
        "A login domain was removed from an identity provider.",
        "Logins on that domain go to the platform provider again.",
    ),
    _e(
        "idp.domain_verified",
        _AUD,
        "outcome",
        "",
        "A login domain was verified by its TXT record.",
        "Logins on that domain now go to the organisation provider.",
    ),
    _e(
        "idp.key_rotated",
        _AUD,
        "outcome",
        "",
        "An identity provider's client secret was replaced.",
        "The sealed client secret changed.",
    ),
    _e(
        "idp.test_login",
        _AUD,
        "outcome",
        "",
        "A test login through an identity provider: {outcome}.",
        "An admin tried the provider before switching it on.",
    ),
    _e(
        "idp.updated",
        _AUD,
        "outcome",
        "",
        "An identity provider was changed.",
        "Its settings changed; `detail` names the fields.",
    ),
    _e(
        "idp_mapping.exported",
        _AUD,
        "outcome",
        "",
        "The login mapping was exported.",
        "An admin downloaded the claim mapping for the identity provider team.",
    ),
    _e(
        "integration.check",
        _SYS,
        "outcome",
        "",
        "Integration check of a group: {outcome} ({reason}).",
        "A group's Webex and Contact Center connection was checked, by the worker or an admin.",
        "On failure: open the group's *Integrations*; it names the provider's reason.",
    ),
    _e(
        "integration.health",
        _SYS,
        "outcome",
        "platform from to requests failures why",
        "The {platform} connection went from {from} to {to}: {failures} of {requests} requests "
        "failed ({why}).",
        "The health of a connection changed (ADR 0082 point 7), computed from the request events "
        "of the last `integration_health_window_minutes`; `down` is an outage, `degraded` a "
        "warning, and the return to `ok` after `integration_recovery_minutes` closes the alert.",
        "`down` with `token_refresh`: authorise the Service App again on *Koppelingen*. With "
        "`failures`: the booking timelines name the provider's reasons. With `probe`: run the "
        "group check.",
    ),
    _e(
        "integration.webhooks_missing",
        _SYS,
        "outcome",
        "count",
        "The reconcile found {count} task states the webhooks did not bring.",
        "The worker asked the contact center about tasks that stayed queued or connected (ADR "
        "0082 point 6) and found a different state: the webhook subscription may be `inactive` "
        "or the webhook address unreachable. A run that finds none closes the alert.",
        "On *Koppelingen*, check the webhook subscription and *Last events*; re-register the "
        "webhook (runbook, section 6).",
    ),
    _e(
        "integration.request",
        _SYS,
        "outcome",
        "method path status ms tracking version platform",
        "{method} {path} for booking {booking_id} answered {status} in {ms} ms: {outcome}.",
        "One request in a booking's chain to Webex or Contact Center (ADR 0065).",
        "On failure: the booking's timeline shows the provider's reason.",
    ),
    _e(
        "integration.service_app_check",
        _AUD,
        "outcome",
        "",
        "The Webex service app was checked: {outcome}.",
        "An admin checked the platform's Webex service app.",
    ),
    _e(
        "logging.destination",
        _AUD,
        "outcome",
        "change kind destination what",
        "Log destination {destination} ({kind}) {change}: {outcome}.",
        "A log destination was created, changed, tested or deleted (audit), or started or "
        "stopped failing (system, ADR 0085).",
        "On failure: open *Log destinations*; it shows the last error.",
    ),
    _e(
        "maintenance.overdue",
        _SYS,
        "warning outcome",
        "hours",
        "Maintenance mode has been on for {hours} hours.",
        "Maintenance mode is on longer than planned (ADR 0084).",
        "Switch it off when the work is done: `python -m app.cli maintenance off`.",
    ),
    _e(
        "organisation.closure",
        _AUD,
        "outcome",
        "",
        "An organisation-wide closure changed.",
        "An emergency closure for the whole organisation was set or ended (ADR 0024).",
        _ADMIN_SEEN,
    ),
    _e(
        "organisation.idp_key_changed",
        _AUD,
        "outcome",
        "",
        "An organisation's identity provider key changed.",
        "The organisation's staff now sign in through another provider.",
        _ADMIN_SEEN,
    ),
    _e(
        "planner.changes_alert",
        _SYS,
        "outcome",
        "",
        "Planners were told about changes.",
        "The alert about changed bookings went out (ADR 0044).",
    ),
    _e(
        "public_address",
        _SYS,
        "outcome",
        "",
        "Public address round trip: {outcome} ({reason}).",
        "The worker's round trip through the public address (ADR 0086 point 2).",
        "On failure: *System status* names the finding.",
    ),
    _e(
        "request.rate_limited",
        _SEC,
        "outcome",
        "surface client",
        "A caller went over a rate limit on {surface}.",
        "Too many requests from one address (ADR 0053); the caller is a pseudonym.",
        "Repeated: a bot or a misconfigured client.",
    ),
    _e(
        "request.rejected",
        _SEC,
        "info outcome",
        "expected origin reason sec_fetch_site",
        "A request was rejected before it reached the application: {reason}.",
        "A honeypot field, a cross-site POST or a foreign `Origin` (ADR 0053).",
        "Repeated `origin_mismatch` from your own site: check the proxy's `Host` header.",
    ),
    _e(
        "retention.sweep",
        _SYS,
        "warning outcome",
        "failed overdue purged",
        "Retention sweep: {overdue} bookings overdue, {purged} deleted, {failed} failed.",
        "The nightly sweep found bookings whose personal data was not yet deleted and deleted "
        "them.",
        "When `failed` is not zero: look at the worker's log; see also `retention.backlog`.",
    ),
    _e(
        "role.created",
        _AUD,
        "outcome",
        "",
        "A role was created.",
        "A custom role was added to the catalogue (ADR 0096).",
        _ADMIN_SEEN,
    ),
    _e(
        "role.deleted",
        _AUD,
        "outcome",
        "",
        "A role was deleted.",
        "A custom role was removed.",
        _ADMIN_SEEN,
    ),
    _e(
        "role.updated",
        _AUD,
        "outcome",
        "",
        "A role was changed.",
        "A custom role's rights changed.",
        _ADMIN_SEEN,
    ),
    _e(
        "simulator.event",
        _AUD,
        "outcome",
        "",
        "A simulated provider event was played.",
        "An admin played an event of the integration simulator.",
    ),
    _e(
        "simulator.prepare",
        _AUD,
        "outcome",
        "",
        "A simulated booking was prepared.",
        "An admin prepared a booking for the integration simulator.",
    ),
    _e(
        "staff.created",
        _AUD,
        "outcome",
        "",
        "A staff member was added.",
        "A person was added to the staff directory (ADR 0046).",
        _ADMIN_SEEN,
    ),
    _e(
        "staff.erased",
        _AUD,
        "info outcome",
        "staff",
        "Staff erased after their retention.",
        "Suspended staff past their retention were erased (ADR 0046).",
    ),
    _e(
        "staff.membership_changed",
        _AUD,
        "outcome",
        "",
        "A staff member's roles or groups changed: {detail}.",
        "`detail` names the role or grant added or removed.",
        _ADMIN_SEEN,
    ),
    _e(
        "staff.reinstated",
        _AUD,
        "outcome",
        "",
        "A staff member was reinstated.",
        "A suspended person can sign in again.",
        _ADMIN_SEEN,
    ),
    _e(
        "staff.suspended",
        _AUD,
        "outcome",
        "",
        "A staff member was suspended.",
        "The person can no longer sign in; their sessions ended.",
        _ADMIN_SEEN,
    ),
    _e(
        "storage.check",
        _SYS,
        "outcome",
        "free_percent database_mb",
        "Storage check: {outcome} ({reason}); {free_percent}% free, database {database_mb} MB.",
        "The worker's disk check changed grade (ADR 0040 point 3).",
        "On failure: free disk space on the host.",
    ),
    _e(
        "queue.wait",
        _SYS,
        "outcome",
        "minutes",
        "Queue wait: {outcome} ({reason}); longest wait {minutes} minutes.",
        "An own queue's longest wait went above `queue_wait_warning_minutes` (failure, the alert) "
        "or back under it (success) (ADR 0061 OV-7).",
        "On failure: more staff on the group's queue, or look at why nobody picks up.",
    ),
    _e(
        "system.maintenance",
        _AUD,
        "outcome",
        "what hours",
        "Maintenance mode: {what}.",
        "Maintenance mode was switched on or off, or a change was refused (ADR 0084).",
        _ADMIN_SEEN,
    ),
    _e(
        "system.update",
        _SYS,
        "outcome",
        "what from to",
        "Update step {what} from {from} to {to}: {outcome}.",
        "A step of a requested update or restore (ADR 0059, 0067); `detail` holds the versions.",
        "On failure: open *Updates*; the runbook explains a rollback.",
    ),
    _e(
        "system.version",
        _SYS,
        "outcome",
        "what from to commit",
        "The running version changed from {from} to {to} (commit {commit}).",
        "At start-up the web process saw another version or commit than last time.",
    ),
    _e(
        "tls.certificate",
        _SYS,
        "warning outcome",
        "finding host",
        "Certificate check of {host}: {finding}.",
        "The certificate the public address serves expires soon or does not fit (ADR 0064).",
        "Renew or replace the certificate (*Certificate* in the portal, or the host's own proxy).",
    ),
    _e(
        "token.rejected",
        _SEC,
        "outcome",
        "",
        "A token or link that matches nothing was used: {reason}.",
        "A guessed, forged or long-expired token or management link (ADR 0053).",
        "Repeated from one caller: a guessing attempt.",
    ),
    _e(
        "webex.token_refresh",
        _SEC,
        "outcome",
        "",
        "Refreshing the Webex token failed: {reason}.",
        "Webex refused or could not be reached for a token refresh (at most one per window).",
        "`unauthorized`: authorise the service app again. `unreachable`: check the network.",
    ),
    _e(
        "webhook.rejected",
        _SEC,
        "warning outcome",
        "source",
        "A webhook from {source} was rejected: its signature did not verify.",
        "A webhook with a wrong signature or an old timestamp (CLAUDE.md, 300 s).",
        "Repeated: check the webhook secret, or someone is posting to the endpoint.",
    ),
)

ENTRIES: tuple[LogEvent, ...] = _TRACE_POINTS + _OPERATIONS + _SECURITY + _AUTHZ + _AUDIT
"""Every listed action, in the order of the generated documentation."""

EVENTS: Mapping[str, LogEvent] = {entry.action: entry for entry in ENTRIES}
"""Every listed action by its key."""


def _permission(permission: Permission) -> LogEvent:
    """The entry of an audit event whose action is a permission."""
    return LogEvent(
        permission.value,
        LogCategory.AUDIT,
        (BY_OUTCOME,),
        (),
        f"Admin action under {permission.value}: {{outcome}}.",
        "An admin used, or was denied, this right; `docs/authorization.md` says who holds it.",
        "On `denied`: nothing, unless it repeats for one admin.",
    )


def _scope(scope: ApiScope) -> LogEvent:
    """The entry of an audit event whose action is a machine API scope."""
    return LogEvent(
        scope.value,
        LogCategory.AUDIT,
        (BY_OUTCOME,),
        (),
        f"Machine API call under scope {scope.value}: {{outcome}}.",
        "A machine API client used, or was denied, this scope (ADR 0023).",
    )


def _job(job: JobType) -> LogEvent:
    """The system event of a worker job."""
    return LogEvent(
        f"job.{job.value}",
        LogCategory.SYSTEM,
        (BY_OUTCOME,),
        (),
        f"Job {job.value} for booking {{booking_id}}: {{outcome}}.",
        "The outcome of one job of the worker; the booking's timeline shows it.",
        "On `failure`: the timeline has the provider's reason.",
    )


FAMILIES: tuple[LogEvent, ...] = (
    *(_permission(p) for p in Permission),
    *(_scope(s) for s in ApiScope),
    *(_job(j) for j in JobType),
)
"""The generated entries: one per permission, API scope and job type."""

_ALL: Mapping[str, LogEvent] = {**{e.action: e for e in FAMILIES}, **EVENTS}


def lookup(action: str) -> LogEvent | None:
    """Return the entry of ``action``, listed or generated; ``None`` when it is not registered."""
    return _ALL.get(action)


def all_events() -> Iterator[LogEvent]:
    """Yield every entry, listed first and generated after."""
    yield from ENTRIES
    yield from (e for e in FAMILIES if e.action not in EVENTS)


LIBRARY_PREFIX = "library."
"""Action of a line from a library's own logger: ``library.<logger>`` (ADR 0103 point 8)."""

MISSING = "-"
"""What a message shows for a placeholder whose field the line does not carry."""


class _Lenient(Formatter):
    """Formats a template, writing ``MISSING`` for an absent or empty field instead of failing.

    A message must never be the reason a line is lost; the scan test keeps the templates and
    the fields in step, this only covers a field that a call left out.
    """

    def get_value(self, key: int | str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> Any:
        """Return the field, or ``MISSING``."""
        value = kwargs.get(str(key))
        return MISSING if value is None or value == "" else value

    def format_field(self, value: Any, format_spec: str) -> str:
        """Write the value as text, a yes/no as ``yes`` or ``no``; templates use no spec."""
        if isinstance(value, bool):
            return "yes" if value else "no"
        if value in ("true", "false"):
            return "yes" if value == "true" else "no"
        return str(value)


def describe(action: str, values: Mapping[str, object]) -> str:
    """Return the readable sentence of ``action`` with ``values`` filled in (ADR 0103 point 4).

    The values are what the line carries, after the PII mask; an unregistered action is returned
    as it is, so a library's own text stays its message.
    """
    entry = lookup(action)
    if entry is None:
        return action
    return _Lenient().format(entry.message, **values)


def describe_event(
    action: str,
    values: Mapping[str, object],
    *,
    outcome: str,
    reason: str,
    organisation: str | None,
    group: str | None,
) -> str:
    """Return the sentence of an audit, system or security event (ADR 0103 point 4).

    The register's sentence says what was done; an event also says how it ended and where.
    When the template does not name them itself, an outcome other than success is added with
    its reason ("An identity provider was changed. Outcome: denied (out_of_scope).": nothing
    changed), and so is the organisation and group the event belongs to.
    """
    entry = lookup(action)
    template = entry.message if entry is not None else ""
    parts = [describe(action, values)]
    why = f" ({reason})" if reason and "{reason}" not in template else ""
    if outcome != "success" and "{outcome}" not in template:
        parts.append(f"Outcome: {outcome}{why}.")
    elif outcome != "success" and why:
        parts.append(f"Reason: {reason}.")
    if organisation and "{organisation}" not in template:
        place = f", group {group}" if group and "{group}" not in template else ""
        parts.append(f"Organisation {organisation}{place}.")
    return " ".join(parts)


def trace_points() -> dict[str, tuple[str, ...]]:
    """Return every trace point with the only fields it may carry (ADR 0085 point 9)."""
    return {e.action: e.fields for e in ENTRIES if e.is_trace_point}
