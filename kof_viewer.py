"""Standalone KOF 2D/3D viewer with triangulation, volume and section tools.

A small Tkinter + matplotlib application for inspecting KOF point files.

Features
--------
- Loads one or more KOF files (via CLI args or the "Open..." button).
- Parses 05 coordinate rows (both ``05 DB Pkt Tk X Y H ...`` and
  ``05 Pkt [Code] X Y H`` variants).
- Preserves polyline grouping using the ``09 91`` (start) and
  ``09 99`` / ``09 96`` (end) delimiters, so points inside a block are
  rendered as a connected line.
- Draws 2D (Easting vs Northing) and 3D (adds elevation) tabs that share
  the same loaded data.
- Toggles for point labels, markers, lines, and triangulation overlay.
- Triangulates loaded files (Delaunay on XY) and computes the volume
  between a selected top and bottom surface over their overlap, with
  cut/fill split.
- Section tool: click two points on the 2D plot to draw a profile that
  shows both surfaces along the line and the area between them.

Only ``matplotlib``, ``numpy`` and ``scipy`` plus the standard library
(``tkinter``) are required; ``geopandas`` is intentionally not used.

Usage
-----
    python kof_viewer.py                       # start empty, use Open...
    python kof_viewer.py file1.kof file2.kof   # preload files
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, ttk
from typing import Iterable, Optional

import matplotlib
import numpy as np
from matplotlib import image as mpimg
try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is typically present via matplotlib
    Image = None

matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)
from matplotlib.figure import Figure
from matplotlib.path import Path
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (registers 3d projection)
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KOFPoint:
    pkt: str
    code: str
    x: float  # Northing
    y: float  # Easting
    h: float  # Elevation
    line_number: int


@dataclass
class KOFBlock:
    """A sequence of points. ``is_polyline`` is True for 09 91..09 99 groups."""

    points: list[KOFPoint] = field(default_factory=list)
    is_polyline: bool = False


@dataclass
class KOFFile:
    path: str
    blocks: list[KOFBlock]
    _tri: Optional[Delaunay] = None
    _xy: Optional[np.ndarray] = None
    _z: Optional[np.ndarray] = None
    _interp: Optional[LinearNDInterpolator] = None
    _kept_mask: Optional[np.ndarray] = None     # bool array over simplices
    _max_edge_factor: float = 2.5                # alpha-shape tightness

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def all_points(self) -> list[KOFPoint]:
        return [p for b in self.blocks for p in b.points]

    def ensure_triangulation(self) -> bool:
        """Compute (or reuse) a Delaunay triangulation on (Easting, Northing).

        Returns True when a valid triangulation is available.
        """
        if self._tri is not None:
            return True
        pts = self.all_points
        if len(pts) < 3:
            return False
        # Deduplicate on (y, x) to avoid Qhull coplanar/degenerate issues; keep
        # the first elevation seen for each unique XY.
        seen: dict[tuple[float, float], float] = {}
        for p in pts:
            key = (p.y, p.x)
            if key not in seen:
                seen[key] = p.h
        if len(seen) < 3:
            return False
        xy = np.array(list(seen.keys()), dtype=float)
        z = np.array(list(seen.values()), dtype=float)
        try:
            tri = Delaunay(xy)
        except Exception:  # noqa: BLE001
            return False
        self._xy = xy
        self._z = z
        self._tri = tri
        self._interp = LinearNDInterpolator(tri, z)
        self._update_boundary_mask()
        return True

    def set_boundary_factor(self, factor: float) -> None:
        """Set the max-edge-length factor (relative to median) for filtering.

        Smaller factor = tighter concave boundary (drops more long triangles).
        """
        self._max_edge_factor = max(float(factor), 0.1)
        if self._tri is not None:
            self._update_boundary_mask()

    def _update_boundary_mask(self) -> None:
        """Flag simplices whose longest edge exceeds factor * median edge."""
        if self._tri is None or self._xy is None:
            self._kept_mask = None
            return
        simp = self._tri.simplices
        if len(simp) == 0:
            self._kept_mask = np.zeros(0, dtype=bool)
            return
        tri_pts = self._xy[simp]  # (nT, 3, 2)
        d01 = np.linalg.norm(tri_pts[:, 0] - tri_pts[:, 1], axis=1)
        d12 = np.linalg.norm(tri_pts[:, 1] - tri_pts[:, 2], axis=1)
        d20 = np.linalg.norm(tri_pts[:, 2] - tri_pts[:, 0], axis=1)
        max_edge = np.maximum(np.maximum(d01, d12), d20)
        median_max = float(np.median(max_edge))
        if median_max <= 0:
            self._kept_mask = np.ones(len(simp), dtype=bool)
            return
        threshold = self._max_edge_factor * median_max
        self._kept_mask = max_edge <= threshold

    @property
    def kept_simplices(self) -> Optional[np.ndarray]:
        if self._tri is None:
            return None
        if self._kept_mask is None:
            return self._tri.simplices
        return self._tri.simplices[self._kept_mask]

    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:
        """Interpolate elevation at (Easting=y, Northing=x).

        Returns NaN outside the filtered (alpha-shape) boundary.
        """
        if self._interp is None and not self.ensure_triangulation():
            return np.full_like(np.asarray(y, dtype=float), np.nan)

        pts = np.column_stack([np.asarray(y, dtype=float),
                               np.asarray(x, dtype=float)])
        z = self._interp(pts)

        if self._kept_mask is not None and self._tri is not None:
            simp_idx = self._tri.find_simplex(pts)
            inside = simp_idx >= 0
            kept = np.zeros_like(inside)
            kept[inside] = self._kept_mask[simp_idx[inside]]
            z = np.where(kept, z, np.nan)
        return z

    def xy_bounds(self) -> Optional[tuple[float, float, float, float]]:
        if self._xy is None and not self.ensure_triangulation():
            return None
        y_min, x_min = self._xy.min(axis=0)
        y_max, x_max = self._xy.max(axis=0)
        return float(y_min), float(x_min), float(y_max), float(x_max)


def _split_tokens(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped:
        return []
    tab_tokens = [part.strip() for part in stripped.split("\t") if part.strip()]
    if len(tab_tokens) >= 2:
        return tab_tokens
    return re.split(r"\s+", stripped)


def _parse_number(value: str) -> float:
    return float(value.replace(",", "."))


def _is_db_header(payload: list[str]) -> bool:
    if len(payload) < 6:
        return False
    return [p.lower() for p in payload[:6]] == ["pkt", "tk", "x", "y", "h", "bk"]


def _parse_05(tokens: list[str], line_number: int) -> Optional[KOFPoint]:
    payload = tokens[1:]
    if not payload:
        return None
    try:
        if payload[0].upper() == "DB":
            payload = payload[1:]
            if _is_db_header(payload) or len(payload) < 5:
                return None
            pkt = payload[0]
            code = payload[1]
            x = _parse_number(payload[2])
            y = _parse_number(payload[3])
            h = _parse_number(payload[4])
            return KOFPoint(pkt, code, x, y, h, line_number)

        if len(payload) >= 4 and payload[0].lower() == "pkt" and payload[1].lower() == "x":
            return None

        if len(payload) >= 5:
            try:
                x = _parse_number(payload[2])
                y = _parse_number(payload[3])
                h = _parse_number(payload[4])
                return KOFPoint(payload[0], payload[1], x, y, h, line_number)
            except ValueError:
                pass

        if len(payload) >= 4:
            x = _parse_number(payload[1])
            y = _parse_number(payload[2])
            h = _parse_number(payload[3])
            return KOFPoint(payload[0], "", x, y, h, line_number)
    except (ValueError, IndexError):
        return None
    return None


def _read_lines(file_path: str) -> Iterable[str]:
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.readlines()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="ISO-8859-1") as fh:
            return fh.readlines()


def parse_kof(file_path: str) -> KOFFile:
    """Parse a KOF file into blocks (polylines + loose points)."""
    blocks: list[KOFBlock] = []
    current: Optional[KOFBlock] = None
    loose = KOFBlock(is_polyline=False)

    for i, line in enumerate(_read_lines(file_path), start=1):
        tokens = _split_tokens(line)
        if not tokens:
            continue
        head = tokens[0]

        if head == "09":
            code = tokens[1] if len(tokens) > 1 else ""
            if code == "91":
                if current is not None and current.points:
                    blocks.append(current)
                current = KOFBlock(is_polyline=True)
            elif code in ("99", "96"):
                if current is not None and current.points:
                    blocks.append(current)
                current = None
            continue

        if head == "05":
            point = _parse_05(tokens, i)
            if point is None:
                continue
            if current is not None:
                current.points.append(point)
            else:
                loose.points.append(point)

    if current is not None and current.points:
        blocks.append(current)
    if loose.points:
        blocks.append(loose)

    return KOFFile(path=file_path, blocks=blocks)


# ---------------------------------------------------------------------------
# Volume calculation
# ---------------------------------------------------------------------------


@dataclass
class VolumeResult:
    top_name: str
    bottom_name: str
    cell_size: float
    n_valid: int
    net: float      # m^3, signed (top - bottom)
    cut: float      # m^3, where top > bottom
    fill: float     # m^3, where top < bottom
    area: float     # m^2, overlap area actually sampled
    y_min: float
    x_min: float
    y_max: float
    x_max: float


_EPS = 1e-12


def _poly_signed_area(poly: list[np.ndarray]) -> float:
    if len(poly) < 3:
        return 0.0
    acc = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        acc += p[0] * q[1] - p[1] * q[0]
    return 0.5 * acc


def _line_intersection(p1: np.ndarray, p2: np.ndarray,
                       a: np.ndarray, b: np.ndarray) -> np.ndarray:
    r = p2 - p1
    s = b - a
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < _EPS:
        return p2.copy()
    ap = a - p1
    t = (ap[0] * s[1] - ap[1] * s[0]) / denom
    return p1 + t * r


def _clip_polygon_convex(subject: list[np.ndarray],
                         clipper: list[np.ndarray]) -> list[np.ndarray]:
    if len(subject) < 3 or len(clipper) < 3:
        return []

    output = [p.copy() for p in subject]
    clip_sign = 1.0 if _poly_signed_area(clipper) >= 0.0 else -1.0
    ccount = len(clipper)

    for i in range(ccount):
        if len(output) < 3:
            return []
        a = clipper[i]
        b = clipper[(i + 1) % ccount]

        def inside(pt: np.ndarray) -> bool:
            edge = b - a
            rel = pt - a
            cross = edge[0] * rel[1] - edge[1] * rel[0]
            return clip_sign * cross >= -_EPS

        input_poly = output
        output = []
        prev = input_poly[-1]
        prev_in = inside(prev)

        for curr in input_poly:
            curr_in = inside(curr)
            if curr_in:
                if not prev_in:
                    output.append(_line_intersection(prev, curr, a, b))
                output.append(curr.copy())
            elif prev_in:
                output.append(_line_intersection(prev, curr, a, b))
            prev = curr
            prev_in = curr_in
    return output


def _point_in_triangle(pt: np.ndarray, tri: list[np.ndarray]) -> bool:
    a, b, c = tri
    v0 = c - a
    v1 = b - a
    v2 = pt - a
    den = v0[0] * v1[1] - v1[0] * v0[1]
    if abs(den) < _EPS:
        return False
    u = (v2[0] * v1[1] - v1[0] * v2[1]) / den
    v = (v0[0] * v2[1] - v2[0] * v0[1]) / den
    return u >= -_EPS and v >= -_EPS and (u + v) <= 1.0 + _EPS


def _triangulate_simple_polygon(poly: list[np.ndarray]) -> list[list[np.ndarray]]:
    if len(poly) < 3:
        return []
    pts = [p.copy() for p in poly]
    if np.linalg.norm(pts[0] - pts[-1]) < _EPS:
        pts.pop()
    if len(pts) < 3:
        return []

    sign = 1.0 if _poly_signed_area(pts) >= 0.0 else -1.0
    indices = list(range(len(pts)))
    triangles: list[list[np.ndarray]] = []
    guard = 0

    while len(indices) > 3 and guard < len(pts) * len(pts):
        guard += 1
        ear_found = False
        n = len(indices)
        for i in range(n):
            i0 = indices[(i - 1) % n]
            i1 = indices[i]
            i2 = indices[(i + 1) % n]
            a = pts[i0]
            b = pts[i1]
            c = pts[i2]

            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if sign * cross <= _EPS:
                continue

            tri = [a, b, c]
            contains_other = False
            for j in indices:
                if j in (i0, i1, i2):
                    continue
                if _point_in_triangle(pts[j], tri):
                    contains_other = True
                    break
            if contains_other:
                continue

            triangles.append([a.copy(), b.copy(), c.copy()])
            indices.pop(i)
            ear_found = True
            break

        if not ear_found:
            return []

    if len(indices) == 3:
        triangles.append([pts[indices[0]].copy(), pts[indices[1]].copy(), pts[indices[2]].copy()])
    return triangles


def _plane_coeff(tri_xy: np.ndarray, tri_z: np.ndarray) -> np.ndarray:
    m = np.column_stack([tri_xy[:, 0], tri_xy[:, 1], np.ones(3)])
    return np.linalg.solve(m, tri_z)


def _integrate_linear_over_polygon(poly: list[np.ndarray], coeff: np.ndarray) -> tuple[float, float]:
    if len(poly) < 3:
        return 0.0, 0.0
    p0 = poly[0]

    def f(pt: np.ndarray) -> float:
        return float(coeff[0] * pt[0] + coeff[1] * pt[1] + coeff[2])

    f0 = f(p0)
    integral = 0.0
    area = 0.0
    for i in range(1, len(poly) - 1):
        p1 = poly[i]
        p2 = poly[i + 1]
        tri_area = abs(0.5 * ((p1[0] - p0[0]) * (p2[1] - p0[1]) -
                              (p1[1] - p0[1]) * (p2[0] - p0[0])))
        if tri_area <= _EPS:
            continue
        f1 = f(p1)
        f2 = f(p2)
        integral += tri_area * (f0 + f1 + f2) / 3.0
        area += tri_area
    return integral, area


def _clip_polygon_by_levelset(poly: list[np.ndarray], coeff: np.ndarray,
                              keep_positive: bool) -> list[np.ndarray]:
    if len(poly) < 3:
        return []

    def g(pt: np.ndarray) -> float:
        return float(coeff[0] * pt[0] + coeff[1] * pt[1] + coeff[2])

    def inside(val: float) -> bool:
        return val >= -_EPS if keep_positive else val <= _EPS

    out: list[np.ndarray] = []
    prev = poly[-1]
    g_prev = g(prev)
    prev_in = inside(g_prev)

    for curr in poly:
        g_curr = g(curr)
        curr_in = inside(g_curr)

        if prev_in != curr_in:
            denom = g_prev - g_curr
            if abs(denom) > _EPS:
                t = g_prev / denom
                t = min(max(t, 0.0), 1.0)
                out.append(prev + t * (curr - prev))
        if curr_in:
            out.append(curr.copy())
        prev = curr
        g_prev = g_curr
        prev_in = curr_in
    return out


def _integrate_diff_over_polygon(poly: list[np.ndarray],
                                 top_xy: np.ndarray, top_z: np.ndarray,
                                 bot_xy: np.ndarray, bot_z: np.ndarray) -> tuple[float, float, float, float]:
    coeff = _plane_coeff(top_xy, top_z) - _plane_coeff(bot_xy, bot_z)

    net, area = _integrate_linear_over_polygon(poly, coeff)
    pos_poly = _clip_polygon_by_levelset(poly, coeff, keep_positive=True)
    neg_poly = _clip_polygon_by_levelset(poly, coeff, keep_positive=False)

    cut_int, _ = _integrate_linear_over_polygon(pos_poly, coeff)
    fill_int, _ = _integrate_linear_over_polygon(neg_poly, coeff)
    cut = max(0.0, cut_int)
    fill = max(0.0, -fill_int)
    return net, cut, fill, area


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
        y_min=y_min, x_min=x_min, y_max=y_max, x_max=x_max,
    )


def _compute_volume_triangles(
    top: KOFFile,
    bottom: KOFFile,
    mask_polygon: Optional[list[tuple[float, float]]] = None,
) -> Optional[VolumeResult]:
    """Integrate (top-bottom) directly over overlap polygons from both triangulations."""
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
        mask_tris = _triangulate_simple_polygon(mask_pts)
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
            overlap_poly = _clip_polygon_convex(top_poly, bot_poly)
            if len(overlap_poly) < 3:
                continue

            pieces = [overlap_poly]
            if mask_tris is not None:
                masked_pieces: list[list[np.ndarray]] = []
                for piece in pieces:
                    for mtri in mask_tris:
                        clipped = _clip_polygon_convex(piece, mtri)
                        if len(clipped) >= 3:
                            masked_pieces.append(clipped)
                pieces = masked_pieces

            for poly in pieces:
                net, cut, fill, area = _integrate_diff_over_polygon(
                    poly, top_xy[i], top_z[i], bot_xy[j], bot_z[j]
                )
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
    return _compute_volume_raster(
        top,
        bottom,
        target_cells=target_cells,
        mask_polygon=mask_polygon,
    )


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------


FILE_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
]


class _HoverTooltip:
    """Small tooltip that appears when hovering over a widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self._show)
        self.widget.bind("<Leave>", self._hide)
        self.widget.bind("<ButtonPress>", self._hide)

    def _show(self, _event=None) -> None:  # noqa: ANN001
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            padx=6,
            pady=4,
        )
        label.pack()
        self.tip_window = tip

    def _hide(self, _event=None) -> None:  # noqa: ANN001
        if self.tip_window is None:
            return
        self.tip_window.destroy()
        self.tip_window = None


class KOFViewer(tk.Tk):
    def __init__(self, initial_files: Optional[list[str]] = None) -> None:
        super().__init__()
        self.title("KOF Viewer – 2D / 3D / Volume / Section")
        self.geometry("1300x860")

        self.files: list[KOFFile] = []
        self.layer_vars: list[tk.BooleanVar] = []
        self.last_volume: Optional[VolumeResult] = None

        self.show_labels = tk.BooleanVar(value=False)
        self.show_markers = tk.BooleanVar(value=True)
        self.show_lines = tk.BooleanVar(value=True)
        self.show_triangulation = tk.BooleanVar(value=False)
        self.equal_aspect = tk.BooleanVar(value=True)
        self.show_raster = tk.BooleanVar(value=False)
        self.raster_alpha = tk.DoubleVar(value=0.7)

        self.top_var = tk.StringVar()
        self.bottom_var = tk.StringVar()
        self.volume_method = tk.StringVar(value="raster")
        self.volume_text = tk.StringVar(value="Volume: (not computed)")
        self.boundary_factor = tk.DoubleVar(value=2.5)

        # Raster background state (2D tab only)
        self.raster_path: Optional[str] = None
        self._raster_img: Optional[np.ndarray] = None
        self._raster_extent: Optional[tuple[float, float, float, float]] = None
        self._raster_origin: str = "upper"
        self._view_extent_2d: Optional[tuple[float, float, float, float]] = None

        # Section-drawing state
        self._section_mode = False
        self._section_click_points: list[tuple[float, float]] = []
        self._section_cid: Optional[int] = None
        self._section_preview = None  # transient Line2D while drawing
        self._sections: list[tuple[tuple[float, float], tuple[float, float]]] = []

        # Polygon mask state for volume limitation in 2D.
        self._mask_mode = False
        self._mask_click_points: list[tuple[float, float]] = []
        self._mask_polygon: list[tuple[float, float]] = []
        self._mask_cid: Optional[int] = None

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        for path in initial_files or []:
            self._load_file(path, redraw=False)
        self._refresh_file_selectors()
        self._refresh_layer_controls()
        self._redraw()

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=4)
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(bar, text="Open...", command=self._on_open).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Clear", command=self._on_clear).pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(bar, text="Open raster...",
                   command=self._on_open_raster).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Clear raster",
                   command=self._on_clear_raster).pack(side=tk.LEFT, padx=2)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        ttk.Checkbutton(bar, text="Raster", variable=self.show_raster,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Label(bar, text="Raster alpha:").pack(side=tk.LEFT, padx=(8, 2))
        self.raster_alpha_spin = ttk.Spinbox(
            bar, from_=0.05, to=1.0, increment=0.05, width=5,
            textvariable=self.raster_alpha,
            command=self._on_raster_alpha_change,
        )
        self.raster_alpha_spin.pack(side=tk.LEFT)
        self.raster_alpha_spin.bind("<Return>",
                                   lambda _e: self._on_raster_alpha_change())
        self.raster_alpha_spin.bind("<FocusOut>",
                                   lambda _e: self._on_raster_alpha_change())

        ttk.Checkbutton(bar, text="Labels", variable=self.show_labels,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(bar, text="Markers", variable=self.show_markers,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(bar, text="Lines", variable=self.show_lines,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(bar, text="Triangulation", variable=self.show_triangulation,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(bar, text="Equal aspect (2D)", variable=self.equal_aspect,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(bar, text="Zoom to selected layers",
               command=self._on_zoom_selected_layers).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Reset view",
               command=self._on_reset_view).pack(side=tk.LEFT, padx=2)

        # Row 2: surfaces + volume + section
        bar2 = ttk.Frame(self, padding=(4, 0, 4, 4))
        bar2.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar2, text="Top:").pack(side=tk.LEFT, padx=(0, 2))
        self.top_combo = ttk.Combobox(bar2, textvariable=self.top_var,
                                      width=28, state="readonly")
        self.top_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(bar2, text="Bottom:").pack(side=tk.LEFT, padx=(10, 2))
        self.bottom_combo = ttk.Combobox(bar2, textvariable=self.bottom_var,
                                         width=28, state="readonly")
        self.bottom_combo.pack(side=tk.LEFT, padx=2)

        ttk.Label(bar2, text="Method:").pack(side=tk.LEFT, padx=(10, 2))
        self.volume_method_combo = ttk.Combobox(
            bar2,
            textvariable=self.volume_method,
            values=["raster", "triangles"],
            width=10,
            state="readonly",
        )
        self.volume_method_combo.pack(side=tk.LEFT, padx=2)
        _HoverTooltip(
            self.volume_method_combo,
            "raster: fastest for quick estimates.\n"
            "triangles: exact overlap integration; slower but more accurate.",
        )

        ttk.Button(bar2, text="Compute volume",
                   command=self._on_compute_volume).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Label(bar2, textvariable=self.volume_text,
                  foreground="#204080").pack(side=tk.LEFT, padx=10)

        ttk.Separator(bar2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Label(bar2, text="Boundary tightness:").pack(side=tk.LEFT, padx=(0, 2))
        self.boundary_spin = ttk.Spinbox(
            bar2, from_=1.0, to=10.0, increment=0.5, width=5,
            textvariable=self.boundary_factor,
            command=self._on_boundary_change,
        )
        self.boundary_spin.pack(side=tk.LEFT)
        self.boundary_spin.bind("<Return>", lambda _e: self._on_boundary_change())
        self.boundary_spin.bind("<FocusOut>", lambda _e: self._on_boundary_change())

        ttk.Separator(bar2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.section_btn = ttk.Button(bar2, text="Draw section",
                                      command=self._on_toggle_section)
        self.section_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(bar2, text="Clear sections",
                   command=self._on_clear_sections).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bar2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.mask_btn = ttk.Button(bar2, text="Draw mask",
                       command=self._on_toggle_mask)
        self.mask_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(bar2, text="Clear mask",
               command=self._on_clear_mask).pack(side=tk.LEFT, padx=2)

    def _build_body(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)

        self.layers_frame = ttk.LabelFrame(body, text="Layers", padding=6)
        self.layers_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 2), pady=(0, 4))
        self.layers_inner = ttk.Frame(self.layers_frame)
        self.layers_inner.pack(fill=tk.BOTH, expand=True)
        self._refresh_layer_controls()

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 2D tab
        frame2d = ttk.Frame(self.notebook)
        self.fig2d = Figure(figsize=(8, 6), dpi=100)
        self.ax2d = self.fig2d.add_subplot(111)
        self.canvas2d = FigureCanvasTkAgg(self.fig2d, master=frame2d)
        self.canvas2d.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar2d = NavigationToolbar2Tk(self.canvas2d, frame2d)
        self.toolbar2d.update()
        self.notebook.add(frame2d, text="2D (plan)")

        # 3D tab
        frame3d = ttk.Frame(self.notebook)
        self.fig3d = Figure(figsize=(8, 6), dpi=100)
        self.ax3d = self.fig3d.add_subplot(111, projection="3d")
        self.canvas3d = FigureCanvasTkAgg(self.fig3d, master=frame3d)
        self.canvas3d.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar3d = NavigationToolbar2Tk(self.canvas3d, frame3d)
        toolbar3d.update()
        self.notebook.add(frame3d, text="3D")

    def _build_statusbar(self) -> None:
        self.status = tk.StringVar(value="No file loaded.")
        bar = ttk.Frame(self, padding=(6, 2))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bar, textvariable=self.status, anchor=tk.W).pack(fill=tk.X)

    # -------------------------------------------------------------- Actions
    def _on_open(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Open KOF file(s)",
            filetypes=[("KOF files", "*.kof"), ("All files", "*.*")],
        )
        for path in paths:
            self._load_file(path, redraw=False)
        self._refresh_file_selectors()
        self._refresh_layer_controls()
        self._redraw()

    def _on_clear(self) -> None:
        self.files.clear()
        self.layer_vars.clear()
        self._view_extent_2d = None
        self.last_volume = None
        self.volume_text.set("Volume: (not computed)")
        self._sections.clear()
        self._mask_polygon.clear()
        self._mask_click_points.clear()
        if self._mask_mode:
            self._end_mask_mode()
        self._refresh_file_selectors()
        self._refresh_layer_controls()
        self._redraw()

    def _on_open_raster(self) -> None:
        path = filedialog.askopenfilename(
            title="Open raster background",
            filetypes=[
                ("Raster images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._load_raster(path)

    def _on_clear_raster(self) -> None:
        had_raster = self._raster_img is not None
        self.raster_path = None
        self._raster_img = None
        self._raster_extent = None
        self.show_raster.set(False)
        if had_raster:
            self._refresh_layer_controls()
            self._redraw()

    def _on_raster_alpha_change(self) -> None:
        try:
            alpha = float(self.raster_alpha.get())
        except (tk.TclError, ValueError):
            return
        alpha = min(max(alpha, 0.05), 1.0)
        self.raster_alpha.set(alpha)
        if self._raster_img is not None and self.show_raster.get():
            self._redraw_2d()

    @staticmethod
    def _worldfile_candidates(raster_path: str) -> list[str]:
        base, ext = os.path.splitext(raster_path)
        ext_l = ext.lower()
        candidates: list[str] = []

        if ext_l in (".jpg", ".jpeg"):
            candidates.extend([base + ".jgw", base + ".jpgw", base + ".jpegw"])
        elif ext_l == ".png":
            candidates.extend([base + ".pgw", base + ".pngw"])
        elif ext_l in (".tif", ".tiff"):
            candidates.extend([base + ".tfw", base + ".tifw", base + ".tiffw"])
        elif ext_l == ".bmp":
            candidates.extend([base + ".bpw", base + ".bmpw"])

        candidates.append(base + ".wld")
        candidates.append(raster_path + "w")

        # Keep order stable and remove duplicates.
        seen: set[str] = set()
        ordered: list[str] = []
        for cand in candidates:
            if cand not in seen:
                seen.add(cand)
                ordered.append(cand)
        return ordered

    @staticmethod
    def _parse_worldfile(path: str) -> tuple[float, float, float, float, float, float]:
        values: list[float] = []
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                txt = raw.strip()
                if not txt:
                    continue
                values.append(float(txt.replace(",", ".")))
        if len(values) < 6:
            raise ValueError("World file must contain at least 6 numeric lines.")
        return values[0], values[1], values[2], values[3], values[4], values[5]

    @staticmethod
    def _read_raster_for_display(
        path: str,
        max_dim: int = 3200,
    ) -> tuple[np.ndarray, int, int]:
        """Return display image plus original raster size (rows, cols).

        Uses a downsampled preview for very large rasters while preserving
        georeferencing based on the original full-resolution dimensions.
        """
        if Image is None:
            arr = mpimg.imread(path)
            if arr.ndim < 2:
                raise ValueError("Unsupported raster format.")
            rows, cols = int(arr.shape[0]), int(arr.shape[1])
            return arr, rows, cols

        old_max_pixels = Image.MAX_IMAGE_PIXELS
        try:
            # Orthophotos can legitimately exceed Pillow's bomb guard threshold.
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as src:
                cols, rows = src.size
                if rows < 1 or cols < 1:
                    raise ValueError("Raster image is empty.")

                img = src
                largest = max(rows, cols)
                if largest > max_dim:
                    scale = max_dim / float(largest)
                    out_cols = max(int(round(cols * scale)), 1)
                    out_rows = max(int(round(rows * scale)), 1)
                    try:
                        img.draft("RGB", (out_cols, out_rows))
                    except Exception:  # noqa: BLE001
                        pass
                    img = img.resize((out_cols, out_rows))

                if img.mode not in ("L", "RGB", "RGBA"):
                    img = img.convert("RGB")
                arr = np.asarray(img)
                return arr, rows, cols
        finally:
            Image.MAX_IMAGE_PIXELS = old_max_pixels

    def _load_raster(self, path: str) -> None:
        worldfile_path = None
        for candidate in self._worldfile_candidates(path):
            if os.path.exists(candidate):
                worldfile_path = candidate
                break

        if worldfile_path is None:
            messagebox.showerror(
                "KOF Viewer",
                "No world file was found for the selected raster.\n\n"
                "Expected one of: .jgw/.pgw/.tfw/.wld beside the image.",
            )
            return

        try:
            img, rows, cols = self._read_raster_for_display(path)
            a, d, b, e, c, f = self._parse_worldfile(worldfile_path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "KOF Viewer",
                f"Failed to load raster or world file:\n{exc}",
            )
            return

        if abs(b) > 1e-12 or abs(d) > 1e-12:
            messagebox.showerror(
                "KOF Viewer",
                "Rotated world files are not supported yet (non-zero B/D terms).",
            )
            return

        if img.ndim < 2:
            messagebox.showerror("KOF Viewer", "Unsupported raster format.")
            return

        x0 = c - (a / 2.0)
        x1 = c + a * (cols - 0.5)
        y0 = f - (e / 2.0)
        y1 = f + e * (rows - 0.5)

        xmin, xmax = (x0, x1) if x0 <= x1 else (x1, x0)
        ymin, ymax = (y0, y1) if y0 <= y1 else (y1, y0)

        self.raster_path = path
        self._raster_img = img
        self._raster_extent = (xmin, xmax, ymin, ymax)
        self._raster_origin = "upper" if e < 0 else "lower"
        self.show_raster.set(True)
        self._refresh_layer_controls()
        self._redraw()

    def _on_reset_view(self) -> None:
        self._view_extent_2d = None
        self._redraw_2d()
        self._update_status()

    def _on_zoom_selected_layers(self) -> None:
        bounds = self._selected_layers_2d_bounds()
        if bounds is None:
            messagebox.showinfo(
                "KOF Viewer",
                "No visible layers with valid extents. Select one or more layers first.",
            )
            return

        xmin, xmax, ymin, ymax = bounds
        span_x = xmax - xmin
        span_y = ymax - ymin
        if span_x <= 0 and span_y <= 0:
            pad_x = 1.0
            pad_y = 1.0
        else:
            pad_x = max(span_x * 0.03, 1.0)
            pad_y = max(span_y * 0.03, 1.0)

        self._view_extent_2d = (
            xmin - pad_x,
            xmax + pad_x,
            ymin - pad_y,
            ymax + pad_y,
        )
        self.notebook.select(0)
        self._redraw_2d()
        self._update_status()

    def _selected_layers_2d_bounds(self) -> Optional[tuple[float, float, float, float]]:
        xmin: Optional[float] = None
        xmax: Optional[float] = None
        ymin: Optional[float] = None
        ymax: Optional[float] = None

        def include(x0: float, x1: float, y0: float, y1: float) -> None:
            nonlocal xmin, xmax, ymin, ymax
            xmin = x0 if xmin is None else min(xmin, x0)
            xmax = x1 if xmax is None else max(xmax, x1)
            ymin = y0 if ymin is None else min(ymin, y0)
            ymax = y1 if ymax is None else max(ymax, y1)

        if self.show_raster.get() and self._raster_extent is not None:
            rxmin, rxmax, rymin, rymax = self._raster_extent
            include(rxmin, rxmax, rymin, rymax)

        for idx, kof in enumerate(self.files):
            if not self._is_layer_visible(idx):
                continue
            points = kof.all_points
            if not points:
                continue
            xs = [p.y for p in points]
            ys = [p.x for p in points]
            include(min(xs), max(xs), min(ys), max(ys))

        if xmin is None or xmax is None or ymin is None or ymax is None:
            return None
        return xmin, xmax, ymin, ymax

    def _on_clear_sections(self) -> None:
        if not self._sections:
            return
        self._sections.clear()
        self._redraw()

    def _load_file(self, path: str, *, redraw: bool = True) -> None:
        try:
            kof = parse_kof(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to read '{path}':\n{exc}")
            return
        self.files.append(kof)
        self.layer_vars.append(tk.BooleanVar(value=True))
        if redraw:
            self._refresh_file_selectors()
            self._refresh_layer_controls()
            self._redraw()

    def _refresh_file_selectors(self) -> None:
        names = [k.name for k in self.files]
        self.top_combo["values"] = names
        self.bottom_combo["values"] = names
        if names:
            if self.top_var.get() not in names:
                self.top_var.set(names[0])
            if self.bottom_var.get() not in names:
                self.bottom_var.set(names[1] if len(names) > 1 else names[0])
        else:
            self.top_var.set("")
            self.bottom_var.set("")

    def _refresh_layer_controls(self) -> None:
        for child in self.layers_inner.winfo_children():
            child.destroy()

        has_raster_layer = self.raster_path is not None and self._raster_img is not None

        if not self.files and not has_raster_layer:
            ttk.Label(self.layers_inner, text="No layers loaded.").pack(anchor="w")
            return

        if has_raster_layer:
            raster_name = os.path.basename(self.raster_path)
            ttk.Checkbutton(
                self.layers_inner,
                text=f"Raster: {raster_name}",
                variable=self.show_raster,
                command=self._redraw,
            ).pack(anchor="w", pady=1)
            ttk.Separator(self.layers_inner, orient=tk.HORIZONTAL).pack(
                fill=tk.X, pady=(2, 4)
            )

        for idx, kof in enumerate(self.files):
            if idx >= len(self.layer_vars):
                self.layer_vars.append(tk.BooleanVar(value=True))

            n_pts = sum(len(b.points) for b in kof.blocks)
            n_pl = sum(1 for b in kof.blocks if b.is_polyline and b.points)
            label = f"{kof.name} ({n_pts} pts, {n_pl} pl)"
            ttk.Checkbutton(
                self.layers_inner,
                text=label,
                variable=self.layer_vars[idx],
                command=self._redraw,
            ).pack(anchor="w", pady=1)

    def _is_layer_visible(self, idx: int) -> bool:
        if idx < 0 or idx >= len(self.layer_vars):
            return True
        return bool(self.layer_vars[idx].get())

    def _find_file(self, name: str) -> Optional[KOFFile]:
        for k in self.files:
            if k.name == name:
                return k
        return None

    def _on_compute_volume(self) -> None:
        top = self._find_file(self.top_var.get())
        bot = self._find_file(self.bottom_var.get())
        if top is None or bot is None:
            messagebox.showwarning("KOF Viewer", "Select both a top and bottom surface.")
            return
        if top is bot:
            messagebox.showwarning("KOF Viewer", "Top and bottom must be different files.")
            return
        mask_poly = self._mask_polygon if len(self._mask_polygon) >= 3 else None
        method = self.volume_method.get().strip().lower()
        result = compute_volume(top, bot, mask_polygon=mask_poly, method=method)
        if result is None:
            messagebox.showerror(
                "KOF Viewer",
                "Could not compute volume. Check that both surfaces have "
                ">= 3 points and that their XY extents overlap.",
            )
            return
        self.last_volume = result
        mask_suffix = " (masked)" if mask_poly is not None else ""
        self.volume_text.set(
            f"{method}: Net {result.net:+,.2f} m³   (Cut {result.cut:,.2f} / "
            f"Fill {result.fill:,.2f})   over {result.area:,.1f} m²{mask_suffix}"
        )
        if self.show_triangulation.get():
            self._redraw()

    def _on_boundary_change(self) -> None:
        try:
            factor = float(self.boundary_factor.get())
        except (tk.TclError, ValueError):
            return
        for kof in self.files:
            if kof._tri is not None or kof.ensure_triangulation():
                kof.set_boundary_factor(factor)
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._redraw()

    # --------------------------------------------------------- Section tool
    def _on_toggle_section(self) -> None:
        if self._mask_mode:
            self._end_mask_mode()
        if self._section_mode:
            self._end_section_mode()
            return
        if len(self.files) < 2:
            messagebox.showinfo("KOF Viewer",
                                "Load at least two files and select Top/Bottom first.")
            return
        if not self.top_var.get() or not self.bottom_var.get():
            messagebox.showinfo("KOF Viewer",
                                "Pick a top and bottom surface before drawing a section.")
            return
        self._section_mode = True
        self._section_click_points = []
        self.section_btn.configure(text="Cancel section")
        self.notebook.select(0)  # switch to 2D tab
        self.status.set("Section mode: click two points on the 2D plot "
                        "(right-click or press Esc to cancel).")
        self._section_cid = self.canvas2d.mpl_connect(
            "button_press_event", self._on_section_click
        )
        self.bind("<Escape>", lambda _e: self._end_section_mode())

    def _end_section_mode(self) -> None:
        self._section_mode = False
        if self._section_cid is not None:
            self.canvas2d.mpl_disconnect(self._section_cid)
            self._section_cid = None
        self.section_btn.configure(text="Draw section")
        self.unbind("<Escape>")
        self._section_click_points = []
        # Drop any in-progress preview; completed sections are kept via
        # self._sections and redrawn by _redraw_2d.
        self._section_preview = None
        self._update_status()

    def _on_section_click(self, event) -> None:  # noqa: ANN001
        if event.inaxes is not self.ax2d:
            return
        if event.button == 3:  # right-click cancels
            self._end_section_mode()
            return
        if event.xdata is None or event.ydata is None:
            return
        self._section_click_points.append((event.xdata, event.ydata))

        if len(self._section_click_points) == 1:
            self.status.set(
                "Section mode: click the second endpoint "
                "(right-click or Esc to cancel)."
            )
            (y0, x0) = self._section_click_points[0]
            self.ax2d.plot([y0], [x0], marker="x", color="black", markersize=8)
            self.canvas2d.draw_idle()
            return

        (y0, x0), (y1, x1) = self._section_click_points
        self._sections.append(((y0, x0), (y1, x1)))

        top = self._find_file(self.top_var.get())
        bot = self._find_file(self.bottom_var.get())
        self._end_section_mode()
        self._redraw_2d()  # draw the persisted section (and clear preview)
        if top is not None and bot is not None:
            SectionWindow(self, top, bot, (y0, x0), (y1, x1),
                          label=self._section_label(len(self._sections) - 1))

    # ----------------------------------------------------------- Polygon mask
    def _on_toggle_mask(self) -> None:
        if self._section_mode:
            self._end_section_mode()
        if self._mask_mode:
            self._end_mask_mode()
            self._redraw_2d()
            return
        self._mask_mode = True
        self._mask_click_points = []
        self.mask_btn.configure(text="Cancel mask")
        self.notebook.select(0)  # switch to 2D tab
        self.status.set(
            "Mask mode: left-click to add polygon vertices. "
            "Right-click (or double-click) to close. Esc to cancel."
        )
        self._mask_cid = self.canvas2d.mpl_connect(
            "button_press_event", self._on_mask_click
        )
        self.bind("<Escape>", lambda _e: self._cancel_mask_mode())

    def _cancel_mask_mode(self) -> None:
        self._mask_click_points = []
        self._end_mask_mode()
        self._redraw_2d()

    def _end_mask_mode(self) -> None:
        self._mask_mode = False
        if self._mask_cid is not None:
            self.canvas2d.mpl_disconnect(self._mask_cid)
            self._mask_cid = None
        self.mask_btn.configure(text="Draw mask")
        self.unbind("<Escape>")
        self._update_status()

    def _on_clear_mask(self) -> None:
        had_mask = len(self._mask_polygon) >= 3
        self._mask_polygon.clear()
        self._mask_click_points.clear()
        if self._mask_mode:
            self._end_mask_mode()
        if had_mask and self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._redraw()

    def _on_mask_click(self, event) -> None:  # noqa: ANN001
        if event.inaxes is not self.ax2d:
            return
        if event.button == 3:  # right-click finalizes, if possible
            if len(self._mask_click_points) >= 3:
                self._mask_polygon = list(self._mask_click_points)
                self._mask_click_points = []
                self._end_mask_mode()
                if self.last_volume is not None:
                    self.volume_text.set("Volume: (stale – recompute)")
                self._redraw()
            else:
                self._cancel_mask_mode()
            return
        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return

        self._mask_click_points.append((event.xdata, event.ydata))
        if event.dblclick and len(self._mask_click_points) >= 3:
            self._mask_polygon = list(self._mask_click_points)
            self._mask_click_points = []
            self._end_mask_mode()
            if self.last_volume is not None:
                self.volume_text.set("Volume: (stale – recompute)")
            self._redraw()
            return

        self.status.set(
            f"Mask mode: {len(self._mask_click_points)} vertex/vertices. "
            "Left-click to add more, right-click or double-click to close."
        )
        self._redraw_2d()

    @staticmethod
    def _section_label(index: int) -> str:
        """Return A, B, ..., Z, AA, AB, ... for the given 0-based index."""
        letters = ""
        n = index
        while True:
            letters = chr(ord("A") + (n % 26)) + letters
            n = n // 26 - 1
            if n < 0:
                break
        return letters

    # --------------------------------------------------------------- Render
    def _redraw(self) -> None:
        self._redraw_2d()
        self._redraw_3d()
        self._update_status()

    def _redraw_2d(self) -> None:
        self.ax2d.clear()
        self.ax2d.set_xlabel("Easting (Y)")
        self.ax2d.set_ylabel("Northing (X)")
        self.ax2d.grid(True, linestyle=":", alpha=0.5)

        if (
            self.show_raster.get()
            and self._raster_img is not None
            and self._raster_extent is not None
        ):
            self.ax2d.imshow(
                self._raster_img,
                extent=self._raster_extent,
                origin=self._raster_origin,
                alpha=float(self.raster_alpha.get()),
                zorder=0,
            )

        for idx, kof in enumerate(self.files):
            if not self._is_layer_visible(idx):
                continue
            color = FILE_COLORS[idx % len(FILE_COLORS)]
            labeled = False
            for block in kof.blocks:
                if not block.points:
                    continue
                xs = [p.y for p in block.points]
                ys = [p.x for p in block.points]
                label = kof.name if not labeled else None
                labeled = True
                if block.is_polyline and self.show_lines.get() and len(block.points) >= 2:
                    self.ax2d.plot(xs, ys, "-", color=color, linewidth=1.2, label=label)
                    if self.show_markers.get():
                        self.ax2d.plot(xs, ys, "o", color=color, markersize=3)
                else:
                    marker = "o" if self.show_markers.get() else "."
                    self.ax2d.plot(xs, ys, marker, color=color, markersize=4,
                                   linestyle="None", label=label)

                if self.show_labels.get():
                    for p in block.points:
                        self.ax2d.annotate(
                            p.pkt, (p.y, p.x), fontsize=7,
                            xytext=(3, 3), textcoords="offset points", color=color,
                        )

            if self.show_triangulation.get() and kof.ensure_triangulation():
                kept = kof.kept_simplices
                if kept is not None and len(kept) > 0:
                    self.ax2d.triplot(
                        kof._xy[:, 0], kof._xy[:, 1], kept,
                        color=color, linewidth=0.4, alpha=0.5,
                    )

        if self.equal_aspect.get():
            self.ax2d.set_aspect("equal", adjustable="datalim")
        else:
            self.ax2d.set_aspect("auto")

        if self._view_extent_2d is not None:
            xmin, xmax, ymin, ymax = self._view_extent_2d
            self.ax2d.set_xlim(xmin, xmax)
            self.ax2d.set_ylim(ymin, ymax)

        # Persisted section lines (drawn on top of surface data).
        for i, ((y0, x0), (y1, x1)) in enumerate(self._sections):
            label = self._section_label(i)
            self.ax2d.plot([y0, y1], [x0, x1], "-", color="black", linewidth=1.6,
                           zorder=5)
            self.ax2d.plot([y0, y1], [x0, x1], "s", color="black", markersize=4,
                           zorder=6)
            # Label the two endpoints as A / A' (etc.)
            self.ax2d.annotate(
                label, (y0, x0), fontsize=9, fontweight="bold", color="black",
                xytext=(6, 6), textcoords="offset points", zorder=7,
            )
            self.ax2d.annotate(
                f"{label}'", (y1, x1), fontsize=9, fontweight="bold", color="black",
                xytext=(6, 6), textcoords="offset points", zorder=7,
            )

        # Active polygon mask.
        if len(self._mask_polygon) >= 3:
            arr = np.asarray(self._mask_polygon, dtype=float)
            closed = np.vstack([arr, arr[0]])
            self.ax2d.fill(closed[:, 0], closed[:, 1],
                           color="#ffbf00", alpha=0.15, zorder=8)
            self.ax2d.plot(closed[:, 0], closed[:, 1], "-", color="#cc8a00",
                           linewidth=1.8, zorder=9)
            self.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#cc8a00",
                           markersize=4, zorder=10)

        # In-progress polygon while mask mode is active.
        if self._mask_mode and self._mask_click_points:
            arr = np.asarray(self._mask_click_points, dtype=float)
            self.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#cc8a00",
                           linewidth=1.3, zorder=11)
            self.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#cc8a00",
                           markersize=4, zorder=12)
            if len(arr) >= 2:
                self.ax2d.plot([arr[-1, 0], arr[0, 0]], [arr[-1, 1], arr[0, 1]],
                               ":", color="#cc8a00", linewidth=1.0, zorder=11)

        if self.files:
            self.ax2d.legend(loc="best", fontsize=8)
        else:
            self.ax2d.text(
                0.5, 0.5, "Open a .kof file to begin",
                transform=self.ax2d.transAxes, ha="center", va="center", color="gray",
            )

        self.fig2d.tight_layout()
        self.canvas2d.draw_idle()

    def _redraw_3d(self) -> None:
        self.ax3d.clear()
        self.ax3d.set_xlabel("Easting (Y)")
        self.ax3d.set_ylabel("Northing (X)")
        self.ax3d.set_zlabel("Elevation (H)")

        for idx, kof in enumerate(self.files):
            if not self._is_layer_visible(idx):
                continue
            color = FILE_COLORS[idx % len(FILE_COLORS)]
            labeled = False
            if self.show_triangulation.get() and kof.ensure_triangulation():
                kept = kof.kept_simplices
                if kept is not None and len(kept) > 0:
                    self.ax3d.plot_trisurf(
                        kof._xy[:, 0], kof._xy[:, 1], kof._z,
                        triangles=kept,
                        color=color, alpha=0.35, linewidth=0.2, edgecolor=color,
                    )
            for block in kof.blocks:
                if not block.points:
                    continue
                xs = [p.y for p in block.points]
                ys = [p.x for p in block.points]
                zs = [p.h for p in block.points]
                label = kof.name if not labeled else None
                labeled = True
                if block.is_polyline and self.show_lines.get() and len(block.points) >= 2:
                    self.ax3d.plot(xs, ys, zs, "-", color=color,
                                   linewidth=1.2, label=label)
                    if self.show_markers.get():
                        self.ax3d.scatter(xs, ys, zs, color=color, s=10)
                else:
                    if self.show_markers.get():
                        self.ax3d.scatter(xs, ys, zs, color=color, s=12, label=label)

                if self.show_labels.get():
                    for p in block.points:
                        self.ax3d.text(p.y, p.x, p.h, p.pkt, fontsize=7, color=color)

        if self.files:
            self.ax3d.legend(loc="best", fontsize=8)
            self._equalize_3d_aspect()
        else:
            self.ax3d.text2D(
                0.5, 0.5, "Open a .kof file to begin",
                transform=self.ax3d.transAxes, ha="center", va="center", color="gray",
            )

        self.fig3d.tight_layout()
        self.canvas3d.draw_idle()

    def _equalize_3d_aspect(self) -> None:
        all_x: list[float] = []
        all_y: list[float] = []
        all_z: list[float] = []
        for kof in self.files:
            for p in kof.all_points:
                all_x.append(p.y)
                all_y.append(p.x)
                all_z.append(p.h)
        if not all_x:
            return
        rx = max(all_x) - min(all_x)
        ry = max(all_y) - min(all_y)
        rz = max(all_z) - min(all_z)
        r = max(rx, ry, rz, 1e-6)
        cx = (max(all_x) + min(all_x)) / 2
        cy = (max(all_y) + min(all_y)) / 2
        cz = (max(all_z) + min(all_z)) / 2
        self.ax3d.set_xlim(cx - r / 2, cx + r / 2)
        self.ax3d.set_ylim(cy - r / 2, cy + r / 2)
        self.ax3d.set_zlim(cz - r / 2, cz + r / 2)
        try:
            self.ax3d.set_box_aspect((1, 1, 1))
        except Exception:  # noqa: BLE001
            pass

    def _update_status(self) -> None:
        if self._section_mode:
            return
        if self._mask_mode:
            return
        if not self.files:
            self.status.set("No file loaded.")
            return
        parts = []
        total_points = 0
        total_blocks = 0
        for kof in self.files:
            n_pts = sum(len(b.points) for b in kof.blocks)
            n_pl = sum(1 for b in kof.blocks if b.is_polyline and b.points)
            total_points += n_pts
            total_blocks += n_pl
            parts.append(f"{kof.name}: {n_pts} pts / {n_pl} polylines")
        base = (
            f"{len(self.files)} file(s), {total_points} points, "
            f"{total_blocks} polylines   |   " + "  ·  ".join(parts)
        )
        if len(self._mask_polygon) >= 3:
            base += f"   |   mask: {len(self._mask_polygon)} vertices"
        if self.raster_path and self._raster_extent is not None:
            state = "on" if self.show_raster.get() else "off"
            base += (
                f"   |   raster: {os.path.basename(self.raster_path)} "
                f"({state}, alpha={float(self.raster_alpha.get()):.2f})"
            )
        self.status.set(base)


# ---------------------------------------------------------------------------
# Section window
# ---------------------------------------------------------------------------


class SectionWindow(tk.Toplevel):
    """Toplevel showing a vertical cross-section between two surfaces."""

    def __init__(
        self,
        master: tk.Misc,
        top: KOFFile,
        bottom: KOFFile,
        p0: tuple[float, float],
        p1: tuple[float, float],
        n_samples: int = 200,
        label: str = "",
    ) -> None:
        super().__init__(master)
        title_label = f" ({label}–{label}')" if label else ""
        self.title(
            f"Section{title_label}: {top.name} (top) vs {bottom.name} (bottom)"
        )
        self.geometry("900x520")

        y0, x0 = p0
        y1, x1 = p1
        total_len = float(np.hypot(y1 - y0, x1 - x0))
        if total_len <= 0:
            ttk.Label(self, text="Zero-length section.").pack(padx=20, pady=20)
            return

        ts = np.linspace(0.0, 1.0, n_samples)
        ys = y0 + (y1 - y0) * ts
        xs = x0 + (x1 - x0) * ts
        dists = ts * total_len

        z_top = top.interpolate(ys, xs)
        z_bot = bottom.interpolate(ys, xs)

        # Area between curves (m² of cross-section); split into cut (top above
        # bottom) and fill (top below bottom).
        diff = z_top - z_bot
        mask = np.isfinite(diff)
        if mask.any():
            d_valid = dists[mask]
            diff_valid = diff[mask]
            # Positive and negative integrals via trapezoid rule.
            pos = np.clip(diff_valid, 0, None)
            neg = np.clip(diff_valid, None, 0)
            area_cut = float(np.trapezoid(pos, d_valid))
            area_fill = float(-np.trapezoid(neg, d_valid))
            area_net = float(np.trapezoid(diff_valid, d_valid))
        else:
            area_cut = area_fill = area_net = float("nan")

        fig = Figure(figsize=(8, 4.5), dpi=100)
        ax = fig.add_subplot(111)

        if np.any(np.isfinite(z_top)):
            ax.plot(dists, z_top, color="#1f77b4", linewidth=1.6,
                    label=f"Top: {top.name}")
        if np.any(np.isfinite(z_bot)):
            ax.plot(dists, z_bot, color="#d62728", linewidth=1.6,
                    label=f"Bottom: {bottom.name}")

        # Shade the area between surfaces where both are defined.
        both = np.isfinite(z_top) & np.isfinite(z_bot)
        if both.any():
            cut_mask = both & (z_top >= z_bot)
            fill_mask = both & (z_top < z_bot)
            if cut_mask.any():
                ax.fill_between(dists, z_bot, z_top, where=cut_mask,
                                color="#1f77b4", alpha=0.25, label="Cut (top ≥ bottom)")
            if fill_mask.any():
                ax.fill_between(dists, z_bot, z_top, where=fill_mask,
                                color="#d62728", alpha=0.25, label="Fill (top < bottom)")

        ax.set_xlabel("Distance along section (m)")
        ax.set_ylabel("Elevation (m)")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(loc="best", fontsize=8)
        ax.set_title(
            f"Length {total_len:,.2f} m   |   "
            f"Area net {area_net:+,.2f} m²   "
            f"(cut {area_cut:,.2f} / fill {area_fill:,.2f})"
        )

        canvas = FigureCanvasTkAgg(fig, master=self)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(canvas, self)
        toolbar.update()

        info = ttk.Label(
            self,
            text=(
                f"From ({x0:,.2f} N, {y0:,.2f} E) "
                f"to ({x1:,.2f} N, {y1:,.2f} E)   "
                f"length={total_len:,.2f} m, {n_samples} samples"
            ),
            anchor=tk.W, padding=(8, 2),
        )
        info.pack(side=tk.BOTTOM, fill=tk.X)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone 2D/3D viewer for KOF files.")
    parser.add_argument("files", nargs="*", help="KOF files to preload")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    app = KOFViewer(initial_files=args.files)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
