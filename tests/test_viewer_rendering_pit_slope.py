from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from kofvision.pit import PitPolygon, PitSettings, TheoreticalPitSurface
from kofvision.viewer_rendering import _draw_pit_slope_end_lines


class _FlatSurface:
    def __init__(self, z: float, bounds: tuple[float, float, float, float]) -> None:
        self._z = float(z)
        self._bounds = bounds

    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:  # noqa: ARG002
        return np.full(np.asarray(y).shape, self._z, dtype=float)

    def xy_bounds(self):
        return self._bounds


class _Ax2D:
    def __init__(self) -> None:
        self.contour_calls = 0
        self.plot_calls = 0

    def contour(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.contour_calls += 1
        return None

    def plot(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.plot_calls += 1
        return None


class _Viewer:
    def __init__(self) -> None:
        self._pit_polygons = [
            PitPolygon(
                name="Pit 1",
                vertices=[(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0)],
                target_z=0.0,
                visible=True,
            )
        ]
        self._pit_settings = PitSettings(edge_slope=0.01, vertical_wall=False, stop_material=None, stop_slot=None)
        self._active_pit_index = 0
        self.top_var = SimpleNamespace(get=lambda: "top")
        self.ax2d = _Ax2D()
        self._top_surface = _FlatSurface(10.0, (-2000.0, -2000.0, 2000.0, 2000.0))

    def _pit_is_visible(self, idx: int) -> bool:
        return idx == 0

    def _find_volume_surface(self, name: str):
        if name == "top":
            return self._top_surface
        return None

    def _pit_surface_for_indices(self, indices: list[int]):
        polygons = [self._pit_polygons[i] for i in indices]
        return TheoreticalPitSurface(polygons, self._pit_settings)


def test_draw_pit_slope_end_lines_finds_shallow_daylight_line() -> None:
    viewer = _Viewer()
    _draw_pit_slope_end_lines(viewer)
    assert viewer.ax2d.contour_calls >= 1
