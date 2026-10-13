from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional


class MaskDataError(ValueError):
    """Raised when a mask JSON file is malformed."""


@dataclass
class MaskDefinition:
    name: str
    vertices: list[tuple[float, float]]
    visible: bool = True


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


def _normalize_vertices(vertices_raw: Any, *, context: str) -> list[tuple[float, float]]:
    if not isinstance(vertices_raw, list):
        raise MaskDataError(f"Invalid mask file: '{context}' must be an array.")
    vertices = [_as_vertex(v, index=i) for i, v in enumerate(vertices_raw)]
    if vertices and len(vertices) < 3:
        raise MaskDataError("Invalid mask file: polygon must have at least 3 vertices.")
    return vertices


def _default_mask_name(index: int) -> str:
    letters = ""
    n = index
    while True:
        letters = chr(ord("A") + (n % 26)) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return f"Mask {letters}"


def save_mask_json(
    path: str,
    vertices: list[tuple[float, float]],
    *,
    metadata: Optional[dict[str, Any]] = None,
    name: Optional[str] = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "kofvision.mask",
        "version": 1,
        "name": name or "Mask A",
        "vertices": [[float(y), float(x)] for y, x in vertices],
    }
    if metadata:
        payload["metadata"] = metadata

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def save_masks_json(
    path: str,
    masks: list[MaskDefinition],
    *,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    payload: dict[str, Any] = {
        "type": "kofvision.mask_set",
        "version": 1,
        "masks": [
            {
                "name": (m.name.strip() or _default_mask_name(i)),
                "vertices": [[float(y), float(x)] for y, x in m.vertices],
                "visible": bool(m.visible),
            }
            for i, m in enumerate(masks)
            if len(m.vertices) >= 3
        ],
    }
    if metadata:
        payload["metadata"] = metadata

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def load_masks_json(path: str) -> list[MaskDefinition]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if not isinstance(payload, dict):
        raise MaskDataError("Invalid mask file: root JSON value must be an object.")

    file_type = payload.get("type")
    if file_type not in (None, "kofvision.mask", "kofvision.mask_set"):
        raise MaskDataError(
            f"Invalid mask file: unsupported type '{file_type}'."
        )

    version = payload.get("version", 1)
    if version != 1:
        raise MaskDataError(f"Invalid mask file: unsupported version '{version}'.")

    if isinstance(payload.get("masks"), list):
        masks_out: list[MaskDefinition] = []
        for idx, item in enumerate(payload["masks"]):
            if not isinstance(item, dict):
                raise MaskDataError(
                    f"Invalid mask at index {idx}: expected object with name/vertices."
                )
            name_raw = item.get("name")
            name = str(name_raw).strip() if name_raw is not None else ""
            if not name:
                name = _default_mask_name(idx)
            vertices = _normalize_vertices(item.get("vertices"), context=f"masks[{idx}].vertices")
            visible = bool(item.get("visible", True))
            if len(vertices) >= 3:
                masks_out.append(MaskDefinition(name=name, vertices=vertices, visible=visible))
        return masks_out

    vertices = _normalize_vertices(payload.get("vertices"), context="vertices")
    name_raw = payload.get("name")
    name = str(name_raw).strip() if name_raw is not None else ""
    if not name:
        name = _default_mask_name(0)
    if len(vertices) < 3:
        return []
    return [MaskDefinition(name=name, vertices=vertices, visible=True)]


def load_mask_json(path: str) -> list[tuple[float, float]]:
    masks = load_masks_json(path)
    if not masks:
        return []
    return list(masks[0].vertices)
