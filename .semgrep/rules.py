# Test cases for rules.yml: `make semgrep` runs them before scanning app/.
import structlog

log = structlog.get_logger()


def examples(booking, email):
    # ruleid: no-print
    print("hello")

    # ruleid: no-pii-in-log-call
    log.info("booking.created", email=email)

    # ruleid: no-pii-in-log-call
    log.warning("booking.failed", booking.phone)

    # ruleid: no-pii-in-log-call
    log.bind(host_join_url=booking.host_join_url)

    # ruleid: no-pii-in-log-call
    log.error(f"failed for {booking.name}")

    # ruleid: no-pii-in-log-call
    log.info("booking.created", first_name=booking.first_name)

    # ruleid: no-pii-in-log-call
    log.info("booking.created", form_answers=booking.form_answers)

    # ok: no-pii-in-log-call
    log.info("booking.created", booking_id=booking.id)
