from __future__ import annotations

import pytest

from kofvision.volume import (
    ConstantElevationSurface,
    SplitVolumeResult,
    VolumeResult,
    compute_volume,
    compute_volume_with_intermediate,
)

BOUNDS = (0.0, 0.0, 10.0, 10.0)  # y_min, x_min, y_max, x_max
AREA = (BOUNDS[2] - BOUNDS[0]) * (BOUNDS[3] - BOUNDS[1])


def _surface(name: str, z_value: float) -> ConstantElevationSurface:
    return ConstantElevationSurface(name=name, z_value=z_value, bounds=BOUNDS)


def test_compute_volume_constant_surfaces_regression() -> None:
    top = _surface("top", 10.0)
    bottom = _surface("bottom", 7.0)

    result = compute_volume(top, bottom, target_cells=200, method="raster")

    assert isinstance(result, VolumeResult)
    assert result is not None
    assert result.top_name == "top"
    assert result.bottom_name == "bottom"
    assert result.area == pytest.approx(AREA)
    assert result.net == pytest.approx(3.0 * AREA)
    assert result.cut == pytest.approx(3.0 * AREA)
    assert result.fill == pytest.approx(0.0)


def test_compute_volume_with_intermediate_split_math() -> None:
    top = _surface("top", 10.0)
    intermediate = _surface("mid", 8.0)
    bottom = _surface("bottom", 7.0)

    result = compute_volume_with_intermediate(
        top,
        intermediate,
        bottom,
        target_cells=200,
        method="raster",
    )

    assert isinstance(result, SplitVolumeResult)
    assert result is not None

    assert result.upper.top_name == "top"
    assert result.upper.bottom_name == "mid"
    assert result.upper.net == pytest.approx(2.0 * AREA)
    assert result.upper.cut == pytest.approx(2.0 * AREA)
    assert result.upper.fill == pytest.approx(0.0)

    assert result.lower.top_name == "mid"
    assert result.lower.bottom_name == "bottom"
    assert result.lower.net == pytest.approx(1.0 * AREA)
    assert result.lower.cut == pytest.approx(1.0 * AREA)
    assert result.lower.fill == pytest.approx(0.0)

    assert result.net == pytest.approx(3.0 * AREA)
    assert result.cut == pytest.approx(3.0 * AREA)
    assert result.fill == pytest.approx(0.0)


def test_compute_volume_with_intermediate_matches_pairwise_sum() -> None:
    top = _surface("top", 12.5)
    intermediate = _surface("mid", 9.0)
    bottom = _surface("bottom", 4.5)

    split = compute_volume_with_intermediate(top, intermediate, bottom, target_cells=180, method="raster")
    upper = compute_volume(top, intermediate, target_cells=180, method="raster")
    lower = compute_volume(intermediate, bottom, target_cells=180, method="raster")

    assert split is not None
    assert upper is not None
    assert lower is not None
    assert split.net == pytest.approx(upper.net + lower.net)
    assert split.cut == pytest.approx(upper.cut + lower.cut)
    assert split.fill == pytest.approx(upper.fill + lower.fill)


def test_mask_polygon_limits_split_volume_area() -> None:
    top = _surface("top", 10.0)
    intermediate = _surface("mid", 8.0)
    bottom = _surface("bottom", 7.0)

    # Half-domain mask: y in [0, 5], x in [0, 10]
    mask = [(0.0, 0.0), (5.0, 0.0), (5.0, 10.0), (0.0, 10.0)]

    split = compute_volume_with_intermediate(
        top,
        intermediate,
        bottom,
        target_cells=200,
        mask_polygon=mask,
        method="raster",
    )

    assert split is not None
    expected_area = 50.0
    assert split.upper.area == pytest.approx(expected_area, abs=1.0)
    assert split.lower.area == pytest.approx(expected_area, abs=1.0)
    assert split.upper.net == pytest.approx(2.0 * expected_area, abs=2.0)
    assert split.lower.net == pytest.approx(1.0 * expected_area, abs=2.0)


def test_triangles_method_supports_constant_split_surfaces() -> None:
    top = _surface("top", 10.0)
    bottom = _surface("bottom", 7.0)
    mid = _surface("mid", 8.0)

    result = compute_volume(top, bottom, method="triangles")
    split = compute_volume_with_intermediate(top, mid, bottom, method="triangles")
    assert result is not None
    assert split is not None
    assert result.area == pytest.approx(AREA)
    assert result.net == pytest.approx(3.0 * AREA)
    assert split.upper.net == pytest.approx(2.0 * AREA)
    assert split.lower.net == pytest.approx(AREA)
    assert split.net == pytest.approx(result.net)
