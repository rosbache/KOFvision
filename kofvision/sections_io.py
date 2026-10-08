from __future__ import annotations

import json
from typing import Any


class SectionDataError(ValueError):
    """Raised when a section definition file is malformed."""


SectionLine = tuple[tuple[float, float], tuple[float, float]]


def _as_point(value: Any, *, index: int, key: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SectionDataError(
            f"Invalid section at index {index}: '{key}' must be [easting, northing]."
        )
    try:
        y = float(value[0])
        x = float(value[1])
    except (TypeError, ValueError) as exc:
        raise SectionDataError(
            f"Invalid section at index {index}: '{key}' coordinates must be numeric."
        ) from exc
    return y, x


def save_sections_json(path: str, sections: list[SectionLine]) -> None:
    payload = {
        "type": "kofvision.sections",
        "version": 1,
        "sections": [
            {
                "start": [float(start[0]), float(start[1])],
                "end": [float(end[0]), float(end[1])],
            }
            for start, end in sections
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def load_sections_json(path: str) -> list[SectionLine]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if not isinstance(payload, dict):
        raise SectionDataError("Invalid section file: root JSON value must be an object.")

    file_type = payload.get("type")
    if file_type not in (None, "kofvision.sections"):
        raise SectionDataError(
            f"Invalid section file: unsupported type '{file_type}'."
        )

    version = payload.get("version", 1)
    if version != 1:
        raise SectionDataError(
            f"Invalid section file: unsupported version '{version}'."
        )

    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list):
        raise SectionDataError("Invalid section file: 'sections' must be an array.")

    sections: list[SectionLine] = []
    for idx, item in enumerate(raw_sections):
        if not isinstance(item, dict):
            raise SectionDataError(
                f"Invalid section at index {idx}: expected object with start/end."
            )
        start = _as_point(item.get("start"), index=idx, key="start")
        end = _as_point(item.get("end"), index=idx, key="end")
        sections.append((start, end))
    return sections
