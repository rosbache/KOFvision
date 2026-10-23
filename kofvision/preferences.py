from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict

from kofvision.localization import normalize_language
from kofvision.validation import safe_float

PREFERENCES_DIR = os.path.join(os.path.expanduser("~"), ".kofvision")
PREFERENCES_PATH = os.path.join(PREFERENCES_DIR, "preferences.json")


@dataclass
class ViewerPreferences:
    language: str = "en"
    theme: str = "dark"
    volume_method: str = "raster"
    station_interval_m: float = 10.0
    station_label_format: str = "plain"
    show_labels: bool = False
    show_markers: bool = True
    show_lines: bool = True
    show_triangulation: bool = False


def load_preferences(path: str = PREFERENCES_PATH) -> ViewerPreferences:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError, TypeError):
        return ViewerPreferences()

    if not isinstance(payload, dict):
        return ViewerPreferences()

    prefs = ViewerPreferences()
    prefs.language = normalize_language(str(payload.get("language", prefs.language)))

    theme = str(payload.get("theme", prefs.theme)).strip().lower()
    prefs.theme = theme if theme in {"dark", "light"} else prefs.theme

    method = str(payload.get("volume_method", prefs.volume_method)).strip().lower()
    prefs.volume_method = method if method in {"raster", "triangles"} else prefs.volume_method

    fmt = str(payload.get("station_label_format", prefs.station_label_format)).strip().lower()
    prefs.station_label_format = fmt if fmt in {"plain", "chainage"} else prefs.station_label_format

    prefs.station_interval_m = safe_float(payload.get("station_interval_m"), default=prefs.station_interval_m, min_value=1.0)
    prefs.show_labels = bool(payload.get("show_labels", prefs.show_labels))
    prefs.show_markers = bool(payload.get("show_markers", prefs.show_markers))
    prefs.show_lines = bool(payload.get("show_lines", prefs.show_lines))
    prefs.show_triangulation = bool(payload.get("show_triangulation", prefs.show_triangulation))
    return prefs


def save_preferences(preferences: ViewerPreferences, path: str = PREFERENCES_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(asdict(preferences), fh, indent=2)
