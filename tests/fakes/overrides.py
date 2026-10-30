from collections.abc import Mapping


class DictOverrides:
    """OverrideSource fake keyed by (organisation_id or None, locale, key)."""

    def __init__(self, texts: Mapping[tuple[int | None, str, str], str] | None = None) -> None:
        self.texts = dict(texts or {})

    def get(self, locale: str, key: str, organisation_id: int | None) -> str | None:
        return self.texts.get((organisation_id, locale, key))

    async def refresh(self) -> None:
        return None
