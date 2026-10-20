from __future__ import annotations

import numpy as np
import pytest

from kofvision.models import KOFBlock, KOFFile, KOFPoint, LandXMLSurface
from kofvision.raster import ElevationRasterSurface
from kofvision.volume import (
    ConstantElevationSurface,
    SurfaceLike,
    VolumeResult,
    compute_volume,
    compute_volume_with_intermediate,
)


BOUNDS = (100.0, 200.0, 106.0, 204.0)
AREA = 24.0
SOURCES = ("kof", "landxml", "dem_upper", "dem_lower", "constant")


def make_surface(kind: str, height: float) -> SurfaceLike:
    y0, x0, y1, x1 = BOUNDS
    if kind == "constant":
        return ConstantElevationSurface(f"plane {height}", height, BOUNDS)
    if kind.startswith("dem"):
        return ElevationRasterSurface(
            f"{kind} {height}", np.full((3, 4), height), (y0, y1, x0, x1), kind.removeprefix("dem_")
        )
    points = [(y0, x0, height), (y1, x0, height), (y1, x1, height), (y0, x1, height)]
    if kind == "landxml":
        return LandXMLSurface("source.xml", str(height), points, [(0, 1, 2), (0, 2, 3)])
    return KOFFile(
        f"source_{height}.kof",
        [KOFBlock([KOFPoint(str(i), "", x, y, z, i + 1) for i, (y, x, z) in enumerate(points)])],
    )


@pytest.mark.parametrize("method", ["raster", "triangles"])
@pytest.mark.parametrize("top_kind", SOURCES)
@pytest.mark.parametrize("bottom_kind", SOURCES)
def test_all_source_pairs_work_in_both_directions(method: str, top_kind: str, bottom_kind: str) -> None:
    top = make_surface(top_kind, 10.0)
    bottom = make_surface(bottom_kind, 7.0)
    forward = compute_volume(top, bottom, method=method, target_cells=11)
    reverse = compute_volume(bottom, top, method=method, target_cells=11)
    assert isinstance(forward, VolumeResult)
    assert isinstance(reverse, VolumeResult)
    assert forward.top_name == top.name
    assert forward.bottom_name == bottom.name
    assert forward.area == pytest.approx(AREA)
    assert reverse.area == pytest.approx(AREA)
    assert forward.net == pytest.approx(3.0 * AREA)
    assert forward.cut == pytest.approx(3.0 * AREA)
    assert forward.fill == pytest.approx(0.0)
    assert reverse.net == pytest.approx(-forward.net)
    assert reverse.fill == pytest.approx(forward.cut)
    assert reverse.cut == pytest.approx(0.0)


@pytest.mark.parametrize("method", ["raster", "triangles"])
@pytest.mark.parametrize("middle_kind", SOURCES)
def test_mixed_split_and_mask(method: str, middle_kind: str) -> None:
    result = compute_volume_with_intermediate(
        make_surface("dem_upper", 10.0),
        make_surface(middle_kind, 8.0),
        make_surface("kof", 7.0),
        method=method,
        target_cells=12,
        mask_polygon=[(100.0, 200.0), (103.0, 200.0), (103.0, 204.0), (100.0, 204.0)],
    )
    assert result is not None
    assert result.upper.area == pytest.approx(AREA / 2)
    assert result.lower.area == pytest.approx(AREA / 2)
    assert result.upper.net == pytest.approx(AREA)
    assert result.lower.net == pytest.approx(AREA / 2)
    assert result.net == pytest.approx(1.5 * AREA)


@pytest.mark.parametrize("origin", ["upper", "lower"])
@pytest.mark.parametrize("method", ["raster", "triangles"])
def test_sloped_dem_orientation_and_cut_fill(origin: str, method: str) -> None:
    ys = np.linspace(100.0, 106.0, 4)
    xs = np.linspace(200.0, 204.0, 3)
    if origin == "upper":
        xs = xs[::-1]
    yy, xx = np.meshgrid(ys, xs)
    values = (yy - 103.0) + (xx - 202.0)
    dem = ElevationRasterSurface("slope", values, (100.0, 106.0, 200.0, 204.0), origin)
    data = dem.triangulation_data()
    assert data is not None
    xy, z, triangles = data
    assert triangles.shape == (12, 3)
    np.testing.assert_allclose(dem.interpolate(xy[:, 0], xy[:, 1]), z)
    result = compute_volume(dem, make_surface("constant", 0.0), method=method, target_cells=60)
    assert result is not None
    assert result.area == pytest.approx(AREA)
    assert result.net == pytest.approx(0.0, abs=1e-10)
    # Integral of max(y + x, 0) over [-3, 3] x [-2, 2].
    assert result.cut == pytest.approx(62.0 / 3.0, abs=0.03)
    assert result.fill == pytest.approx(result.cut)


@pytest.mark.parametrize("method", ["raster", "triangles"])
@pytest.mark.parametrize("nodata", [np.nan, np.inf, -np.inf])
def test_dem_nodata_cells_are_not_bridged(method: str, nodata: float) -> None:
    values = np.full((3, 4), 3.0)
    values[0, 0] = nodata
    dem = ElevationRasterSurface("hole", values, (100.0, 106.0, 200.0, 204.0), "upper")
    result = compute_volume(dem, make_surface("constant", 1.0), method=method, target_cells=12)
    assert result is not None
    assert result.area == pytest.approx(20.0)
    assert result.net == pytest.approx(40.0)


def test_all_nodata_dem_has_no_triangle_volume() -> None:
    dem = ElevationRasterSurface("empty", np.full((3, 4), np.nan), (100.0, 106.0, 200.0, 204.0), "upper")
    assert compute_volume(dem, make_surface("kof", 0.0), method="triangles") is None


@pytest.mark.parametrize("method", ["raster", "triangles"])
def test_disjoint_inputs_have_no_volume(method: str) -> None:
    dem = ElevationRasterSurface("far away", np.ones((2, 2)), (0.0, 1.0, 0.0, 1.0), "upper")
    assert compute_volume(dem, make_surface("landxml", 0.0), method=method) is None


def test_unknown_method_is_not_silently_treated_as_raster() -> None:
    with pytest.raises(ValueError, match="Unknown volume method"):
        compute_volume(make_surface("kof", 2.0), make_surface("dem_upper", 1.0), method="typo")
