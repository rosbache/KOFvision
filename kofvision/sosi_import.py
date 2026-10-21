from __future__ import annotations

from typing import Optional

from sosi_parser import densify_buep, parse_sosi_file, resolve_flate_rings

from .models import KOFBlock, KOFFile, KOFPoint, VectorSurface


def parse_sosi_surface(file_path: str) -> VectorSurface:
    """Parse a SOSI file into a format-independent VectorSurface."""
    parsed = parse_sosi_file(file_path)
    header = parsed.header

    points: list[tuple[float, float, float]] = []
    line_strings: list[list[tuple[float, float, float]]] = []
    labels: list[str] = []
    codes: list[str] = []

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
                points.append((easting, northing, height))
                labels.append(f"SOSI-{obj_id}-{idx}")
                codes.append(obj_code)
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
                line_strings.append([(easting, northing, height) for (northing, easting, height) in ring])
        else:
            coords = obj.scaled_coordinates(
                header.enhet,
                header.origo_n,
                header.origo_e,
                z_override=z_override,
            )

        if coords:
            line_strings.append([(easting, northing, height) for (northing, easting, height) in coords])
            for idx, (northing, easting, height) in enumerate(coords):
                points.append((easting, northing, height))
                labels.append(f"SOSI-{obj_id}-{idx}")
                codes.append(obj_code)

    if not points:
        raise ValueError("SOSI file did not contain supported surface geometry.")

    return VectorSurface(
        path=file_path,
        points=points,
        line_strings=line_strings,
        triangles=[],
        source_kind="sosi",
        point_labels=labels,
        point_codes=codes,
    )


def parse_sosi_as_kof(file_path: str) -> KOFFile:
    """Backwards-compatible adapter that exposes SOSI as legacy KOFFile."""
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
                    KOFPoint(
                        pkt=f"SOSI-{obj_id}-{idx}",
                        code=obj_code,
                        x=float(northing),
                        y=float(easting),
                        h=float(height),
                        line_number=point_counter,
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
        KOFPoint(
            pkt=f"SOSI-{obj_id}-{idx}",
            code=obj_code,
            x=float(northing),
            y=float(easting),
            h=float(height),
            line_number=point_counter_start + idx,
        )
        for idx, (northing, easting, height) in enumerate(coords)
    ]
    blocks.append(KOFBlock(points=points, is_polyline=len(points) >= 2))


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
