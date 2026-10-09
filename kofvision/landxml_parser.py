from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

import numpy as np
from lxml import etree

from .models import LandXMLAlignment, LandXMLFile, LandXMLStation, LandXMLSurface


SCHEMA_BY_NAMESPACE: dict[str, str] = {
    "http://www.landxml.org/schema/LandXML-1.0": "http://www.landxml.org/schema/LandXML-1.0/LandXML-1.0.xsd",
    "http://www.landxml.org/schema/LandXML-1.1": "http://www.landxml.org/schema/LandXML-1.1/LandXML-1.1.xsd",
    "http://www.landxml.org/schema/LandXML-1.2": "http://www.landxml.org/schema/LandXML-1.2/LandXML-1.2.xsd",
}


def _parse_float(value: Optional[str], default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value.replace(",", "."))


def _parse_floats(text: Optional[str]) -> list[float]:
    if text is None:
        return []
    return [float(tok.replace(",", ".")) for tok in text.strip().split() if tok.strip()]


def _parse_xy(text: Optional[str]) -> Optional[tuple[float, float]]:
    vals = _parse_floats(text)
    if len(vals) < 2:
        return None
    # LandXML coordinates are commonly written as Northing Easting [Elevation].
    x_northing = vals[0]
    y_easting = vals[1]
    return (y_easting, x_northing)


def _parse_xyz(text: Optional[str]) -> Optional[tuple[float, float, float]]:
    vals = _parse_floats(text)
    if len(vals) < 3:
        return None
    x_northing = vals[0]
    y_easting = vals[1]
    z = vals[2]
    return (y_easting, x_northing, z)


def _schema_url_from_root(root: etree._Element) -> Optional[str]:
    xsi = root.nsmap.get("xsi")
    if not xsi:
        return None
    schema_location = root.get(f"{{{xsi}}}schemaLocation")
    if not schema_location:
        return None
    parts = schema_location.split()
    if len(parts) < 2:
        return None
    return parts[-1]


def _load_schema_tree(schema_source: str) -> etree._ElementTree:
    if schema_source.lower().startswith(("http://", "https://")):
        with urlopen(schema_source, timeout=10) as response:  # noqa: S310
            payload = response.read()
        return etree.parse(BytesIO(payload))
    return etree.parse(str(Path(schema_source)))


def _validate_against_schema(
    tree: etree._ElementTree,
    *,
    local_schema_path: Optional[str],
    declared_schema_url: Optional[str],
    expected_schema_url: Optional[str],
    allow_network_schema: bool,
) -> tuple[bool, Optional[str], Optional[str], list[str]]:
    errors: list[str] = []
    mode: Optional[str] = None
    schema_source: Optional[str] = None

    if local_schema_path:
        mode = "local"
        schema_source = local_schema_path
    else:
        mode = "network"
        schema_source = declared_schema_url or expected_schema_url
        if schema_source is None:
            errors.append("No schema URL found in document and no local schema path was provided.")
            return False, mode, schema_source, errors
        if not allow_network_schema:
            errors.append("Network schema loading is disabled and no local schema path was provided.")
            return False, mode, schema_source, errors

    try:
        schema_tree = _load_schema_tree(schema_source)
        schema = etree.XMLSchema(schema_tree)
        ok = bool(schema.validate(tree))
        if not ok:
            for err in schema.error_log:
                errors.append(str(err))
        return ok, mode, schema_source, errors
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Schema validation failed to initialize: {exc}")
        return False, mode, schema_source, errors


def _interpolate_polyline(centerline: list[tuple[float, float]], offset: float) -> Optional[tuple[float, float]]:
    if not centerline or len(centerline) < 2 or offset < 0:
        return None

    for i in range(1, len(centerline)):
        y0, x0 = centerline[i - 1]
        y1, x1 = centerline[i]
        seg_len = float(np.hypot(y1 - y0, x1 - x0))
        if seg_len <= 0:
            continue
        if offset <= seg_len:
            t = offset / seg_len
            return (y0 + (y1 - y0) * t, x0 + (x1 - x0) * t)
        offset -= seg_len
    return centerline[-1]


def _interpolate_pvi_z(pvi: list[tuple[float, float]], station: float) -> Optional[float]:
    if not pvi:
        return None
    if station <= pvi[0][0]:
        return pvi[0][1]
    if station >= pvi[-1][0]:
        return pvi[-1][1]

    for i in range(1, len(pvi)):
        s0, z0 = pvi[i - 1]
        s1, z1 = pvi[i]
        if s1 <= s0:
            continue
        if s0 <= station <= s1:
            t = (station - s0) / (s1 - s0)
            return z0 + (z1 - z0) * t
    return None


def _parse_surfaces(root: etree._Element, ns: str, path: str) -> list[LandXMLSurface]:
    out: list[LandXMLSurface] = []
    for surf in root.findall(f".//{{{ns}}}Surfaces/{{{ns}}}Surface"):
        surf_name = surf.get("name") or "surface"
        pnts_node = surf.find(f".//{{{ns}}}Definition/{{{ns}}}Pnts")
        faces_node = surf.find(f".//{{{ns}}}Definition/{{{ns}}}Faces")
        if pnts_node is None:
            continue

        points_by_id: dict[str, tuple[float, float, float]] = {}
        points_in_order: list[tuple[str, tuple[float, float, float]]] = []
        for idx, p in enumerate(pnts_node.findall(f"{{{ns}}}P"), start=1):
            xyz = _parse_xyz(p.text)
            if xyz is None:
                continue
            pid = p.get("id") or str(idx)
            if pid not in points_by_id:
                points_by_id[pid] = xyz
                points_in_order.append((pid, xyz))

        if len(points_in_order) < 3:
            continue

        ordered_points = [xyz for _pid, xyz in points_in_order]
        id_to_idx = {pid: idx for idx, (pid, _xyz) in enumerate(points_in_order)}

        faces: list[tuple[int, int, int]] = []
        if faces_node is not None:
            for f in faces_node.findall(f"{{{ns}}}F"):
                vals = [int(v) for v in f.text.split()] if f.text else []
                if len(vals) != 3:
                    continue
                # Try id-mapped indices first, then LandXML's common 1-based indexing.
                key_vals = [str(v) for v in vals]
                if all(k in id_to_idx for k in key_vals):
                    tri = tuple(id_to_idx[k] for k in key_vals)
                else:
                    tri = tuple(v - 1 for v in vals)
                if min(tri) < 0 or max(tri) >= len(ordered_points):
                    continue
                faces.append(tri)

        out.append(LandXMLSurface(path=path, layer_name=surf_name, points=ordered_points, faces=faces))
    return out


def _parse_alignments(root: etree._Element, ns: str, station_interval: float) -> list[LandXMLAlignment]:
    alignments: list[LandXMLAlignment] = []
    if station_interval <= 0:
        station_interval = 10.0

    for alignment in root.findall(f".//{{{ns}}}Alignments/{{{ns}}}Alignment"):
        name = alignment.get("name") or "alignment"
        sta_start = _parse_float(alignment.get("staStart"), 0.0)
        length = _parse_float(alignment.get("length"), 0.0)

        centerline: list[tuple[float, float]] = []
        coord_geom = alignment.find(f"{{{ns}}}CoordGeom")
        if coord_geom is not None:
            for geom in list(coord_geom):
                start = geom.find(f"{{{ns}}}Start")
                end = geom.find(f"{{{ns}}}End")
                start_xy = _parse_xy(start.text if start is not None else None)
                end_xy = _parse_xy(end.text if end is not None else None)
                if start_xy is not None:
                    if not centerline or centerline[-1] != start_xy:
                        centerline.append(start_xy)
                if end_xy is not None:
                    if not centerline or centerline[-1] != end_xy:
                        centerline.append(end_xy)

        pvi_pairs: list[tuple[float, float]] = []
        for pvi in alignment.findall(f".//{{{ns}}}PVI"):
            vals = _parse_floats(pvi.text)
            if len(vals) >= 2:
                pvi_pairs.append((vals[0], vals[1]))
        pvi_pairs.sort(key=lambda x: x[0])

        stations: list[LandXMLStation] = []
        if centerline and len(centerline) >= 2 and length > 0:
            n_steps = int(math.floor(length / station_interval))
            for i in range(n_steps + 1):
                sta = sta_start + i * station_interval
                if sta > sta_start + length + 1e-6:
                    break
                xy = _interpolate_polyline(centerline, sta - sta_start)
                if xy is None:
                    continue
                z = _interpolate_pvi_z(pvi_pairs, sta)
                label = f"{sta:.0f}"
                stations.append(LandXMLStation(label=label, station=sta, y=xy[0], x=xy[1], z=z))
            end_sta = sta_start + length
            if not stations or abs(stations[-1].station - end_sta) > 1e-6:
                xy = _interpolate_polyline(centerline, length)
                if xy is not None:
                    z = _interpolate_pvi_z(pvi_pairs, end_sta)
                    stations.append(
                        LandXMLStation(
                            label=f"{end_sta:.0f}",
                            station=end_sta,
                            y=xy[0],
                            x=xy[1],
                            z=z,
                        )
                    )

        alignments.append(
            LandXMLAlignment(
                name=name,
                sta_start=sta_start,
                length=length,
                centerline=centerline,
                stations=stations,
            )
        )

    return alignments


def parse_landxml(
    path: str,
    *,
    station_interval: float = 10.0,
    strict_xsd_validation: bool = False,
    local_schema_path: Optional[str] = None,
    allow_network_schema: bool = True,
) -> LandXMLFile:
    tree = etree.parse(path)
    root = tree.getroot()
    if root.tag.startswith("{"):
        ns = root.tag[1:].split("}", 1)[0]
    else:
        ns = ""

    version = root.get("version")
    declared_schema = _schema_url_from_root(root)
    expected_schema = SCHEMA_BY_NAMESPACE.get(ns)

    validation_passed: Optional[bool] = None
    validation_mode: Optional[str] = None
    validation_source: Optional[str] = None
    validation_errors: list[str] = []
    if strict_xsd_validation:
        validation_passed, validation_mode, validation_source, validation_errors = _validate_against_schema(
            tree,
            local_schema_path=local_schema_path,
            declared_schema_url=declared_schema,
            expected_schema_url=expected_schema,
            allow_network_schema=allow_network_schema,
        )

    surfaces = _parse_surfaces(root, ns, path)
    alignments = _parse_alignments(root, ns, station_interval=station_interval)

    return LandXMLFile(
        path=path,
        namespace=ns,
        version=version,
        declared_schema_url=declared_schema,
        expected_schema_url=expected_schema,
        xsd_validation_enabled=strict_xsd_validation,
        xsd_validation_passed=validation_passed,
        xsd_validation_mode=validation_mode,
        xsd_schema_source=validation_source,
        xsd_validation_errors=validation_errors,
        surfaces=surfaces,
        alignments=alignments,
    )
