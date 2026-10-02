from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay


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
    """A sequence of points. is_polyline is True for 09 91..09 99 groups."""

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
    _kept_mask: Optional[np.ndarray] = None  # bool array over simplices
    _max_edge_factor: float = 2.5  # alpha-shape tightness

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def all_points(self) -> list[KOFPoint]:
        return [p for b in self.blocks for p in b.points]

    def ensure_triangulation(self) -> bool:
        """Compute (or reuse) a Delaunay triangulation on (Easting, Northing)."""
        if self._tri is not None:
            return True
        pts = self.all_points
        if len(pts) < 3:
            return False

        # Deduplicate on (y, x) to avoid Qhull coplanar/degenerate issues.
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
        """Set max edge-length factor (relative to median) for filtering."""
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
        """Interpolate elevation at (Easting=y, Northing=x)."""
        if self._interp is None and not self.ensure_triangulation():
            return np.full_like(np.asarray(y, dtype=float), np.nan)

        pts = np.column_stack([np.asarray(y, dtype=float), np.asarray(x, dtype=float)])
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
