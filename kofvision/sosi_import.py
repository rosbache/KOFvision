from __future__ import annotations

from typing import Optional

from sosi_parser import densify_buep, parse_sosi_file, resolve_flate_rings

from .models import KOFBlock, KOFFile, KOFPoint


def parse_sosi_as_kof(file_path: str) -> KOFFile:
    """Parse a SOSI file and map supported geometry into a KOF-like surface."""
    parsed = parse_sosi_file(file_path)
    header = parsed.header

    blocks: list[KOFBlock] = []
    loose = KOFBlock(is_polyline=False)
    point_counter = 1

    for obj_id in sorted(parsed.objects):
        obj = parsed.objects[obj_id]
        obj_code = obj.objtype or obj.object_type
        z_override = _extract_height_override(obj)

        if obj.object_type == "PUNKT":
            coords = obj.scaled_coordinates(
                header.enhet,
                header.origo_n,
                header.origo_e,
                z_override=z_override,
            )
            for idx, (northing, easting, height) in enumerate(coords):
                loose.points.append(
                    _make_point(
                        point_counter,
                        obj_id,
                        idx,
                        northing,
                        easting,
                        height,
                        obj_code,
                    )
                )
                point_counter += 1
            continue

        if obj.object_type == "BUEP":
            coords = densify_buep(obj, header.enhet, header.origo_n, header.origo_e)
            if z_override is not None:
                coords = [(n, e, z_override) for (n, e, _h) in coords]
        elif obj.object_type == "FLATE":
            outer, holes = resolve_flate_rings(
                obj,
                parsed.objects,
                header.enhet,
                header.origo_n,
                header.origo_e,
            )
            coords = outer or []
            for ring in holes:
                _append_polyline_block(blocks, ring, obj_id, obj_code, point_counter)
                point_counter += len(ring)
        else:
            coords = obj.scaled_coordinates(
                header.enhet,
                header.origo_n,
                header.origo_e,
                z_override=z_override,
            )

        _append_polyline_block(blocks, coords, obj_id, obj_code, point_counter)
        point_counter += len(coords)

    if loose.points:
        blocks.append(loose)

    if not blocks:
        raise ValueError("SOSI file did not contain supported surface geometry.")
    return KOFFile(path=file_path, blocks=blocks)


def _append_polyline_block(
    blocks: list[KOFBlock],
    coords: list[tuple[float, float, float]],
    obj_id: int,
    obj_code: str,
    point_counter_start: int,
) -> None:
    if not coords:
        return
    points = [
        _make_point(point_counter_start + idx, obj_id, idx, northing, easting, height, obj_code)
        for idx, (northing, easting, height) in enumerate(coords)
    ]
    blocks.append(KOFBlock(points=points, is_polyline=len(points) >= 2))


def _make_point(
    running_idx: int,
    obj_id: int,
    point_idx: int,
    northing: float,
    easting: float,
    height: float,
    code: str,
) -> KOFPoint:
    return KOFPoint(
        pkt=f"SOSI-{obj_id}-{point_idx}",
        code=code,
        x=float(northing),
        y=float(easting),
        h=float(height),
        line_number=int(running_idx),
    )


def _extract_height_override(obj: object) -> Optional[float]:
    top_level = getattr(obj, "top_level", None)
    nested = getattr(obj, "nested", None)
    if not isinstance(top_level, dict):
        return None
    candidates: list[str] = []
    for key, value in top_level.items():
        if _normalize_key(str(key)) in {"HOYDE", "H"}:
            candidates.append(str(value))
    if isinstance(nested, dict):
        for section in nested.values():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if _normalize_key(str(key)) in {"HOYDE", "H"}:
                    candidates.append(str(value))
    for text in candidates:
        try:
            return float(text.replace(",", ".").split()[0])
        except (ValueError, IndexError):
            continue
    return None


def _normalize_key(key: str) -> str:
    normalized = key.upper()
    normalized = normalized.replace("Ø", "O").replace("Æ", "AE").replace("Å", "A")
    normalized = normalized.replace("-", "_")
    return "".join(ch for ch in normalized if ch.isalnum() or ch == "_")
