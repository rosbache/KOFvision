from __future__ import annotations

import json
from typing import Any, Optional


class MaskDataError(ValueError):
    """Raised when a mask JSON file is malformed."""


def _as_vertex(value: Any, *, index: int) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise MaskDataError(f"Invalid vertex at index {index}: expected [easting, northing].")
    try:
        y = float(value[0])
        x = float(value[1])
    except (TypeError, ValueError) as exc:
        raise MaskDataError(
            f"Invalid vertex at index {index}: coordinates must be numeric."
        ) from exc
    return y, x


def save_mask_json(
    path: str,
    vertices: list[tuple[float, float]],
    *,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "kofvision.mask",
        "version": 1,
        "vertices": [[float(y), float(x)] for y, x in vertices],
    }
    if metadata:
        payload["metadata"] = metadata

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def load_mask_json(path: str) -> list[tuple[float, float]]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if not isinstance(payload, dict):
        raise MaskDataError("Invalid mask file: root JSON value must be an object.")

    file_type = payload.get("type")
    if file_type not in (None, "kofvision.mask"):
        raise MaskDataError(
            f"Invalid mask file: unsupported type '{file_type}'."
        )

    version = payload.get("version", 1)
    if version != 1:
        raise MaskDataError(f"Invalid mask file: unsupported version '{version}'.")

    vertices_raw = payload.get("vertices")
    if not isinstance(vertices_raw, list):
        raise MaskDataError("Invalid mask file: 'vertices' must be an array.")

    vertices = [_as_vertex(v, index=i) for i, v in enumerate(vertices_raw)]
    if vertices and len(vertices) < 3:
        raise MaskDataError("Invalid mask file: polygon must have at least 3 vertices.")
    return vertices
