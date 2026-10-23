from __future__ import annotations

import json

from kofvision import localization
from kofvision.localization import Translator, normalize_language
from kofvision.preferences import ViewerPreferences, load_preferences, save_preferences


def test_norwegian_catalog_translates_defined_values() -> None:
    translator = Translator("nb")

    assert translator.gettext("toolbar.open") == "Åpne"


def test_missing_values_fall_back_to_the_key(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    locales = tmp_path / "locales"
    locales.mkdir()
    (locales / "en.json").write_text(json.dumps({"known": "English fallback"}), encoding="utf-8")
    (locales / "nb.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(localization, "_LOCALES_DIR", locales)

    assert Translator("nb").gettext("known") == "English fallback"
    assert Translator("nb").gettext("missing") == "missing"


def test_language_names_are_normalized_and_unknown_values_use_english() -> None:
    assert normalize_language("no") == "nb"
    assert normalize_language("nb-NO") == "nb"
    assert normalize_language("fr") == "en"


def test_preferences_load_language_and_default_unknown_values_to_english(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"language": "no"}), encoding="utf-8")
    assert load_preferences(str(path)).language == "nb"

    path.write_text(json.dumps({"language": "unsupported"}), encoding="utf-8")
    assert load_preferences(str(path)).language == "en"


def test_preferences_save_selected_language(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    save_preferences(ViewerPreferences(language="nb"), str(path))

    assert load_preferences(str(path)).language == "nb"
