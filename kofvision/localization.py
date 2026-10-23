from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_LOCALES_DIR = Path(__file__).parent / "locales"
_SUPPORTED_LANGUAGES = {"en", "nb"}


def normalize_language(language: str) -> str:
    normalized = language.strip().lower().replace("-", "_")
    normalized = {"no": "nb", "nb_no": "nb"}.get(normalized, normalized)
    return normalized if normalized in _SUPPORTED_LANGUAGES else "en"


class Translator:
    def __init__(self, language: str = "en") -> None:
        self.language = normalize_language(language)
        self._english = self._load_catalog("en")
        self._catalog = self._english if self.language == "en" else self._load_catalog(self.language)

    @staticmethod
    def _load_catalog(language: str) -> dict[str, str]:
        path = _LOCALES_DIR / f"{language}.json"
        with path.open("r", encoding="utf-8") as catalog_file:
            catalog: Any = json.load(catalog_file)
        if not isinstance(catalog, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in catalog.items()
        ):
            raise ValueError(f"Invalid translation catalog: {path}")
        return catalog

    def gettext(self, key: str, **values: Any) -> str:
        text = self._catalog.get(key, self._english.get(key, key))
        return text.format(**values) if values else text
