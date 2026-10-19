from __future__ import annotations

import numpy as np

from kofvision.pit import (
    PitPolygon,
    PitSettings,
    TheoreticalPitSurface,
    parse_pit_polygons,
    parse_pit_settings,
    pit_polygons_to_payload,
)


class _FlatStopSurface:
    def __init__(self, z: float) -> None:
        self._z = float(z)

    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:
        return np.full(y.shape, self._z, dtype=float)


def test_parse_and_payload_roundtrip() -> None:
    polygons = parse_pit_polygons(
        [
            {
                "name": "Pit A",
                "visible": True,
                "target_z": 95.5,
                "vertices": [[0, 0], [0, 10], [10, 10], [10, 0]],
            }
        ]
    )
    assert len(polygons) == 1
    assert polygons[0].name == "Pit A"
    assert polygons[0].target_z == 95.5

    payload = pit_polygons_to_payload(polygons)
    assert payload[0]["target_z"] == 95.5


def test_parse_settings_filters_invalid_values() -> None:
    settings = parse_pit_settings({"edge_slope": "bad", "stop_material": "sand", "stop_slot": 99})
    assert settings.edge_slope == 1.0
    assert settings.vertical_wall is False
    assert settings.stop_material is None
    assert settings.stop_slot is None


def test_parse_settings_reads_vertical_wall_flag() -> None:
    settings = parse_pit_settings({"edge_slope": 2.5, "vertical_wall": True})
    assert settings.edge_slope == 2.5
    assert settings.vertical_wall is True


def test_theoretical_surface_uses_lowest_polygon_on_overlap() -> None:
    polygons = [
        PitPolygon(name="P1", vertices=[(0, 0), (0, 4), (4, 4), (4, 0)], target_z=100.0),
        PitPolygon(name="P2", vertices=[(2, 2), (2, 6), (6, 6), (6, 2)], target_z=95.0),
    ]
    surface = TheoreticalPitSurface(polygons, PitSettings(edge_slope=1.0))

    y = np.array([3.0])
    x = np.array([3.0])
    z = surface.interpolate(y, x)
    assert np.isclose(z[0], 95.0)


def test_theoretical_surface_exposes_xy_bounds() -> None:
    polygons = [
        PitPolygon(name="P1", vertices=[(1, 2), (1, 6), (4, 6), (4, 2)], target_z=100.0),
    ]
    surface = TheoreticalPitSurface(polygons, PitSettings(edge_slope=1.0))
    assert surface.xy_bounds() == (1.0, 2.0, 4.0, 6.0)


def test_theoretical_surface_stop_layer_is_hard_floor() -> None:
    polygons = [
        PitPolygon(name="P1", vertices=[(0, 0), (0, 10), (10, 10), (10, 0)], target_z=90.0),
    ]
    surface = TheoreticalPitSurface(
        polygons,
        PitSettings(edge_slope=1.0),
        stop_surface=_FlatStopSurface(92.0),
    )

    y = np.array([5.0, 12.0])
    x = np.array([5.0, 12.0])
    z = surface.interpolate(y, x)

    assert np.isclose(z[0], 92.0)
    assert z[1] >= 92.0


def test_theoretical_surface_vertical_wall_returns_nan_outside_pit() -> None:
    polygons = [
        PitPolygon(name="P1", vertices=[(0, 0), (0, 10), (10, 10), (10, 0)], target_z=90.0),
    ]
    surface = TheoreticalPitSurface(
        polygons,
        PitSettings(edge_slope=1.0, vertical_wall=True),
    )

    y = np.array([5.0, 12.0])
    x = np.array([5.0, 12.0])
    z = surface.interpolate(y, x)

    assert np.isclose(z[0], 90.0)
    assert np.isnan(z[1])
