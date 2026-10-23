import re
from pathlib import Path

from app.domain.errors import ErrorCode
from app.web.errors import ERROR_KEYS


def test_every_error_code_has_a_message() -> None:
    # A code without a message would turn into a 500 on the generic error page.
    assert set(ERROR_KEYS) == set(ErrorCode)


def test_every_error_message_exists_in_both_catalogues() -> None:
    for locale in ("nl", "en"):
        catalogue = Path(f"app/i18n/locales/{locale}.po").read_text()
        keys = set(re.findall(r'msgid "([^"]+)"', catalogue))
        assert set(ERROR_KEYS.values()) <= keys, locale
