from collections.abc import Mapping


class DictOverrides:
    """OverrideSource fake keyed by (organisation_id or None, locale, key).

    ``group_texts`` holds the groups' own words of the agent term by (group_id, locale, key).
    """

    def __init__(
        self,
        texts: Mapping[tuple[int | None, str, str], str] | None = None,
        group_texts: Mapping[tuple[int, str, str], str] | None = None,
    ) -> None:
        self.texts = dict(texts or {})
        self.group_texts = dict(group_texts or {})

    def get(
        self, locale: str, key: str, organisation_id: int | None, group_id: int | None = None
    ) -> str | None:
        if group_id is not None:
            return self.group_texts.get((group_id, locale, key))
        return self.texts.get((organisation_id, locale, key))

    async def refresh(self) -> None:
        return None
