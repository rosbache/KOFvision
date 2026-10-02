from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from matplotlib.path import Path

from .geometry import (
    _EPS,
    clip_polygon_convex,
    integrate_diff_over_polygon,
    triangulate_simple_polygon,
)
from .models import KOFFile


@dataclass
class VolumeResult:
    top_name: str
    bottom_name: str
    cell_size: float
    n_valid: int
    net: float  # m^3, signed (top - bottom)
    cut: float  # m^3, where top > bottom
    fill: float  # m^3, where top < bottom
    area: float  # m^2, overlap area actually sampled
    y_min: float
    x_min: float
    y_max: float
    x_max: float


def _compute_volume_raster(
    top: KOFFile,
    bottom: KOFFile,
    target_cells: int = 200,
    mask_polygon: Optional[list[tuple[float, float]]] = None,
) -> Optional[VolumeResult]:
    """Grid-sample both surfaces and sum (top-bottom) * cell_area."""
    if not top.ensure_triangulation() or not bottom.ensure_triangulation():
        return None

    tb = top.xy_bounds()
    bb = bottom.xy_bounds()
    if tb is None or bb is None:
        return None
    y_min = max(tb[0], bb[0])
    x_min = max(tb[1], bb[1])
    y_max = min(tb[2], bb[2])
    x_max = min(tb[3], bb[3])
    if y_min >= y_max or x_min >= x_max:
        return None

    span = max(y_max - y_min, x_max - x_min)
    cell = span / max(target_cells, 10)

    ny = max(int(np.ceil((y_max - y_min) / cell)), 2)
    nx = max(int(np.ceil((x_max - x_min) / cell)), 2)
    ys = np.linspace(y_min + cell / 2, y_max - cell / 2, ny)
    xs = np.linspace(x_min + cell / 2, x_max - cell / 2, nx)
    yy, xx = np.meshgrid(ys, xs, indexing="xy")
    yy_f = yy.ravel()
    xx_f = xx.ravel()

    if mask_polygon is not None and len(mask_polygon) >= 3:
        poly = Path(np.asarray(mask_polygon, dtype=float))
        in_poly = poly.contains_points(np.column_stack([yy_f, xx_f]))
    else:
        in_poly = np.ones_like(yy_f, dtype=bool)

    z_top = top.interpolate(yy_f, xx_f)
    z_bot = bottom.interpolate(yy_f, xx_f)
    diff = z_top - z_bot
    mask = np.isfinite(diff) & in_poly
    valid = diff[mask]
    cell_area = (ys[1] - ys[0]) * (xs[1] - xs[0]) if len(ys) > 1 and len(xs) > 1 else cell * cell

    cut = float(valid[valid > 0].sum() * cell_area)
    fill = float(-valid[valid < 0].sum() * cell_area)
    net = float(valid.sum() * cell_area)

    return VolumeResult(
        top_name=top.name,
        bottom_name=bottom.name,
        cell_size=float(cell),
        n_valid=int(mask.sum()),
        net=net,
        cut=cut,
        fill=fill,
        area=float(mask.sum() * cell_area),
        y_min=y_min,
        x_min=x_min,
        y_max=y_max,
        x_max=x_max,
    )


def _compute_volume_triangles(
    top: KOFFile,
    bottom: KOFFile,
    mask_polygon: Optional[list[tuple[float, float]]] = None,
) -> Optional[VolumeResult]:
    """Integrate (top-bottom) over overlap polygons from both triangulations."""
    if not top.ensure_triangulation() or not bottom.ensure_triangulation():
        return None
    if top._xy is None or top._z is None or bottom._xy is None or bottom._z is None:
        return None

    top_simp = top.kept_simplices
    bot_simp = bottom.kept_simplices
    if top_simp is None or bot_simp is None or len(top_simp) == 0 or len(bot_simp) == 0:
        return None

    tb = top.xy_bounds()
    bb = bottom.xy_bounds()
    if tb is None or bb is None:
        return None
    y_min = max(tb[0], bb[0])
    x_min = max(tb[1], bb[1])
    y_max = min(tb[2], bb[2])
    x_max = min(tb[3], bb[3])
    if y_min >= y_max or x_min >= x_max:
        return None

    top_xy = top._xy[top_simp]
    top_z = top._z[top_simp]
    bot_xy = bottom._xy[bot_simp]
    bot_z = bottom._z[bot_simp]

    top_ymin = np.min(top_xy[:, :, 0], axis=1)
    top_ymax = np.max(top_xy[:, :, 0], axis=1)
    top_xmin = np.min(top_xy[:, :, 1], axis=1)
    top_xmax = np.max(top_xy[:, :, 1], axis=1)

    bot_ymin = np.min(bot_xy[:, :, 0], axis=1)
    bot_ymax = np.max(bot_xy[:, :, 0], axis=1)
    bot_xmin = np.min(bot_xy[:, :, 1], axis=1)
    bot_xmax = np.max(bot_xy[:, :, 1], axis=1)

    mask_tris: Optional[list[list[np.ndarray]]] = None
    if mask_polygon is not None and len(mask_polygon) >= 3:
        mask_pts = [np.asarray(p, dtype=float) for p in mask_polygon]
        mask_tris = triangulate_simple_polygon(mask_pts)
        if not mask_tris:
            return None

    net_total = 0.0
    cut_total = 0.0
    fill_total = 0.0
    area_total = 0.0
    n_valid = 0

    for i in range(len(top_xy)):
        overlaps = (
            (bot_ymax >= top_ymin[i] - _EPS)
            & (bot_ymin <= top_ymax[i] + _EPS)
            & (bot_xmax >= top_xmin[i] - _EPS)
            & (bot_xmin <= top_xmax[i] + _EPS)
        )
        cand_idx = np.flatnonzero(overlaps)
        if len(cand_idx) == 0:
            continue

        top_poly = [top_xy[i, 0], top_xy[i, 1], top_xy[i, 2]]
        for j in cand_idx:
            bot_poly = [bot_xy[j, 0], bot_xy[j, 1], bot_xy[j, 2]]
            overlap_poly = clip_polygon_convex(top_poly, bot_poly)
            if len(overlap_poly) < 3:
                continue

            pieces = [overlap_poly]
            if mask_tris is not None:
                masked_pieces: list[list[np.ndarray]] = []
                for piece in pieces:
                    for mtri in mask_tris:
                        clipped = clip_polygon_convex(piece, mtri)
                        if len(clipped) >= 3:
                            masked_pieces.append(clipped)
                pieces = masked_pieces

            for poly in pieces:
                net, cut, fill, area = integrate_diff_over_polygon(poly, top_xy[i], top_z[i], bot_xy[j], bot_z[j])
                if area <= _EPS:
                    continue
                net_total += net
                cut_total += cut
                fill_total += fill
                area_total += area
                n_valid += 1

    if n_valid == 0:
        return None

    return VolumeResult(
        top_name=top.name,
        bottom_name=bottom.name,
        cell_size=0.0,
        n_valid=n_valid,
        net=float(net_total),
        cut=float(cut_total),
        fill=float(fill_total),
        area=float(area_total),
        y_min=y_min,
        x_min=x_min,
        y_max=y_max,
        x_max=x_max,
    )


def compute_volume(
    top: KOFFile,
    bottom: KOFFile,
    target_cells: int = 200,
    mask_polygon: Optional[list[tuple[float, float]]] = None,
    method: str = "raster",
) -> Optional[VolumeResult]:
    """Compute volume by raster sampling or direct triangle integration."""
    mode = (method or "raster").strip().lower()
    if mode == "triangles":
        return _compute_volume_triangles(top, bottom, mask_polygon=mask_polygon)
    return _compute_volume_raster(top, bottom, target_cells=target_cells, mask_polygon=mask_polygon)
