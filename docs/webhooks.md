# Webhooks (Webex Connect)

Status: written in work package 6. Outbound and inbound calls carry
`X-Signature: t=<ts>,v1=<hmac-sha256>` and are rejected if |now − ts| > 300 s (`CLAUDE.md`).
Outbound payload at T−5 min: `{booking_id, first_name, host_join_url, queue_id, locale}`.
