"""Translated texts of the "Koppeling controleren" findings (verify button).

Every finding code from ``app.adapters.group_check`` maps to one catalogue key; the keys are
written out literally so the catalogue test can check them. A code without a key (from a newer
checker) falls back to a generic sentence, so the page never fails on it.
"""

from app.domain.checks import CheckArea, CheckStatus

FINDING_LABELS: dict[str, str] = {
    "platform.not_configured": "admin.check.platform.not_configured",
    "webex.no_host": "admin.check.webex.no_host",
    "webex.host_ok": "admin.check.webex.host_ok",
    "webex.host_unknown": "admin.check.webex.host_unknown",
    "webex.host_refused": "admin.check.webex.host_refused",
    "webex.unreachable": "admin.check.webex.unreachable",
    "wxcc.not_configured": "admin.check.wxcc.not_configured",
    "wxcc.no_mapping": "admin.check.wxcc.no_mapping",
    "wxcc.config_refused": "admin.check.wxcc.config_refused",
    "wxcc.region.ok": "admin.check.wxcc.region.ok",
    "wxcc.region.wrong": "admin.check.wxcc.region.wrong",
    "wxcc.region.no_org": "admin.check.wxcc.region.no_org",
    "wxcc.region.unknown": "admin.check.wxcc.region.unknown",
    "wxcc.channel.ok": "admin.check.wxcc.channel.ok",
    "wxcc.channel.missing": "admin.check.wxcc.channel.missing",
    "wxcc.channel.wrong_type": "admin.check.wxcc.channel.wrong_type",
    "wxcc.asset.ok": "admin.check.wxcc.asset.ok",
    "wxcc.asset.missing": "admin.check.wxcc.asset.missing",
    "wxcc.asset.wrong_channel": "admin.check.wxcc.asset.wrong_channel",
    "wxcc.schema.ok": "admin.check.wxcc.schema.ok",
    "wxcc.schema.refused": "admin.check.wxcc.schema.refused",
    "wxcc.schema.hidden": "admin.check.wxcc.schema.hidden",
    "wxcc.entry.ok": "admin.check.wxcc.entry.ok",
    "wxcc.entry.missing": "admin.check.wxcc.entry.missing",
    "wxcc.entry.no_flow": "admin.check.wxcc.entry.no_flow",
    "wxcc.entry.inactive": "admin.check.wxcc.entry.inactive",
    "wxcc.flow.not_exported": "admin.check.wxcc.flow.not_exported",
    "wxcc.routing.ok": "admin.check.wxcc.routing.ok",
    "wxcc.routing.shared": "admin.check.wxcc.routing.shared",
    "wxcc.routing.not_read": "admin.check.wxcc.routing.not_read",
    "wxcc.branch.ok": "admin.check.wxcc.branch.ok",
    "wxcc.branch.missing": "admin.check.wxcc.branch.missing",
    "wxcc.queues.ok": "admin.check.wxcc.queues.ok",
    "wxcc.queues.none": "admin.check.wxcc.queues.none",
    "wxcc.queues.wrong_type": "admin.check.wxcc.queues.wrong_type",
    "wxcc.naming.ok": "admin.check.wxcc.naming.ok",
    "wxcc.naming.no_match": "admin.check.wxcc.naming.no_match",
    "wxcc.screenpop.ok": "admin.check.wxcc.screenpop.ok",
    "wxcc.screenpop.missing": "admin.check.wxcc.screenpop.missing",
    "webhooks.not_https": "admin.check.webhooks.not_https",
    "webhooks.listing_refused": "admin.check.webhooks.listing_refused",
    "webhooks.webex_ok": "admin.check.webhooks.webex_ok",
    "webhooks.webex_missing": "admin.check.webhooks.webex_missing",
    "webhooks.wxcc_ok": "admin.check.webhooks.wxcc_ok",
    "webhooks.wxcc_missing": "admin.check.webhooks.wxcc_missing",
    "webhooks.stale": "admin.check.webhooks.stale",
    "webhooks.no_secret": "admin.check.webhooks.no_secret",  # nosec B105 - a catalogue key
    "webhooks.delivery_ok": "admin.check.webhooks.delivery_ok",
    "webhooks.delivery_failed": "admin.check.webhooks.delivery_failed",
}

UNKNOWN_FINDING = "admin.check.unknown"

AREA_LABELS: dict[CheckArea, str] = {
    CheckArea.PLATFORM: "admin.check.area.platform",
    CheckArea.WEBEX: "admin.check.area.webex",
    CheckArea.WXCC: "admin.check.area.wxcc",
    CheckArea.WEBHOOKS: "admin.check.area.webhooks",
}

STATUS_LABELS: dict[CheckStatus, tuple[str, str]] = {
    CheckStatus.OK: ("admin.check.status.ok", "success"),
    CheckStatus.WARN: ("admin.check.status.warn", "warning"),
    CheckStatus.FAIL: ("admin.check.status.fail", "danger"),
    CheckStatus.SKIPPED: ("admin.check.status.skipped", ""),
}
"""Label key and chip tone per status; the status is always written out (WCAG 1.4.1)."""
