from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

import numpy as np
from matplotlib.path import Path as MplPath

from kofvision.ditch import DitchLayerCatalog


_MATERIALS = {"rock", "moist"}


class SurfaceLike(Protocol):
    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:
        ...


@dataclass
class PitPolygon:
    name: str
    vertices: list[tuple[float, float]]
    target_z: float
    visible: bool = True


@dataclass
class PitSettings:
    edge_slope: float = 1.0
    vertical_wall: bool = False
    stop_material: Optional[str] = None
    stop_slot: Optional[int] = None


def pit_polygons_to_payload(polygons: list[PitPolygon]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for polygon in polygons:
        if len(polygon.vertices) < 3:
            continue
        payload.append(
            {
                "name": polygon.name,
                "visible": bool(polygon.visible),
                "target_z": float(polygon.target_z),
                "vertices": [[float(y), float(x)] for y, x in polygon.vertices],
            }
        )
    return payload


def parse_pit_polygons(payload: object) -> list[PitPolygon]:
    if not isinstance(payload, list):
        return []
    parsed: list[PitPolygon] = []
    for idx, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        vertices_raw = item.get("vertices")
        if not isinstance(vertices_raw, list):
            continue
        vertices: list[tuple[float, float]] = []
        for vertex in vertices_raw:
            if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
                continue
            try:
                vertices.append((float(vertex[0]), float(vertex[1])))
            except (TypeError, ValueError):
                continue
        if len(vertices) < 3:
            continue
        try:
            target_z = float(item.get("target_z"))
        except (TypeError, ValueError):
            continue
        if not np.isfinite(target_z):
            continue
        name_raw = str(item.get("name", "")).strip()
        name = name_raw or f"Pit {idx + 1}"
        visible = bool(item.get("visible", True))
        parsed.append(PitPolygon(name=name, vertices=vertices, target_z=target_z, visible=visible))
    return parsed


def pit_settings_to_payload(settings: PitSettings) -> dict[str, Any]:
    return {
        "edge_slope": float(settings.edge_slope),
        "vertical_wall": bool(settings.vertical_wall),
        "stop_material": settings.stop_material,
        "stop_slot": settings.stop_slot,
    }


def parse_pit_settings(payload: object) -> PitSettings:
    if not isinstance(payload, dict):
        return PitSettings()

    try:
        edge_slope = float(payload.get("edge_slope", 1.0))
    except (TypeError, ValueError):
        edge_slope = 1.0
    if not np.isfinite(edge_slope):
        edge_slope = 1.0

    vertical_wall = bool(payload.get("vertical_wall", False))

    stop_material_raw = str(payload.get("stop_material", "")).strip().lower()
    stop_material = stop_material_raw if stop_material_raw in _MATERIALS else None

    stop_slot: Optional[int] = None
    raw_slot = payload.get("stop_slot")
    if raw_slot not in (None, ""):
        try:
            stop_slot = int(raw_slot)
        except (TypeError, ValueError):
            stop_slot = None
        if stop_slot is not None and (stop_slot < 0 or stop_slot > 9):
            stop_slot = None

    return PitSettings(
        edge_slope=float(edge_slope),
        vertical_wall=vertical_wall,
        stop_material=stop_material,
        stop_slot=stop_slot,
    )


def resolve_stop_surface(
    settings: PitSettings,
    catalog: DitchLayerCatalog,
    surface_lookup: Mapping[str, SurfaceLike],
) -> Optional[SurfaceLike]:
    if settings.stop_material is None:
        return None
    name = catalog.surface_name(settings.stop_material, settings.stop_slot)
    if not name:
        return None
    return surface_lookup.get(name)


def _point_to_segment_distance(
    py: np.ndarray,
    px: np.ndarray,
    ay: float,
    ax: float,
    by: float,
    bx: float,
) -> np.ndarray:
    vy = by - ay
    vx = bx - ax
    seg_len_sq = (vy * vy) + (vx * vx)
    if seg_len_sq <= 0:
        return np.hypot(py - ay, px - ax)
    t = ((py - ay) * vy + (px - ax) * vx) / seg_len_sq
    t = np.clip(t, 0.0, 1.0)
    qy = ay + t * vy
    qx = ax + t * vx
    return np.hypot(py - qy, px - qx)


def _distance_to_polygon_edges(
    py: np.ndarray,
    px: np.ndarray,
    vertices: list[tuple[float, float]],
) -> np.ndarray:
    n = len(vertices)
    if n < 2:
        return np.full(py.shape, np.inf, dtype=float)

    best = np.full(py.shape, np.inf, dtype=float)
    for idx in range(n):
        ay, ax = vertices[idx]
        by, bx = vertices[(idx + 1) % n]
        best = np.minimum(best, _point_to_segment_distance(py, px, ay, ax, by, bx))
    return best


class TheoreticalPitSurface:
    """Surface evaluator for pit polygons.

    The interior of each polygon is fixed to its own target elevation.
    Outside polygon boundaries, elevation rises linearly away from each edge
    using edge_slope (m/m), unless vertical_wall=True, which keeps values
    undefined outside pits so volumes are limited to pit interiors.
    Multiple polygons combine as lowest-Z-wins.
    """

    def __init__(
        self,
        polygons: list[PitPolygon],
        settings: PitSettings,
        *,
        stop_surface: Optional[SurfaceLike] = None,
        only_visible: bool = True,
    ) -> None:
        self.name = "Pit surface"
        self._polygons = [p for p in polygons if len(p.vertices) >= 3 and (p.visible or not only_visible)]
        self._settings = settings
        self._stop_surface = stop_surface

    def xy_bounds(self) -> Optional[tuple[float, float, float, float]]:
        if not self._polygons:
            return None
        y_min = float("inf")
        x_min = float("inf")
        y_max = float("-inf")
        x_max = float("-inf")
        for polygon in self._polygons:
            arr = np.asarray(polygon.vertices, dtype=float)
            if arr.size == 0:
                continue
            y_min = min(y_min, float(np.min(arr[:, 0])))
            x_min = min(x_min, float(np.min(arr[:, 1])))
            y_max = max(y_max, float(np.max(arr[:, 0])))
            x_max = max(x_max, float(np.max(arr[:, 1])))
        if not np.isfinite(y_min) or not np.isfinite(x_min) or not np.isfinite(y_max) or not np.isfinite(x_max):
            return None
        return (y_min, x_min, y_max, x_max)

    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:
        y_arr = np.asarray(y, dtype=float)
        x_arr = np.asarray(x, dtype=float)
        if y_arr.shape != x_arr.shape:
            raise ValueError("y and x must have matching shapes.")

        result = np.full(y_arr.shape, np.nan, dtype=float)
        if not self._polygons:
            return result

        slope_abs = abs(float(self._settings.edge_slope))
        vertical_wall = bool(self._settings.vertical_wall)
        py = y_arr.reshape(-1)
        px = x_arr.reshape(-1)
        out = result.reshape(-1)

        for polygon in self._polygons:
            path = MplPath(np.asarray(polygon.vertices, dtype=float))
            inside = path.contains_points(np.column_stack((py, px)), radius=1e-12)
            dist = _distance_to_polygon_edges(py, px, polygon.vertices)

            if vertical_wall:
                candidate = np.where(inside, float(polygon.target_z), np.nan)
            else:
                candidate = np.full(py.shape, float(polygon.target_z), dtype=float)
                if slope_abs > 0:
                    candidate = candidate + slope_abs * dist
                # Force exact interior target height independent of edge distance.
                candidate = np.where(inside, float(polygon.target_z), candidate)

            candidate_finite = np.isfinite(candidate)
            out_nan = np.isnan(out)
            write_new = out_nan & candidate_finite
            out[write_new] = candidate[write_new]
            merge_mask = (~out_nan) & candidate_finite
            out[merge_mask] = np.minimum(out[merge_mask], candidate[merge_mask])

        if self._stop_surface is not None:
            z_stop = self._stop_surface.interpolate(y_arr, x_arr).reshape(-1)
            finite = np.isfinite(out) & np.isfinite(z_stop)
            # Stop layer acts as hard floor: pit surface cannot be lower.
            out[finite] = np.maximum(out[finite], z_stop[finite])

        return result