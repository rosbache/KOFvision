from __future__ import annotations

from pathlib import Path

import pytest

from kofvision.sosi_import import parse_sosi_as_kof
from kofvision.volume import ConstantElevationSurface, compute_volume


def test_parse_sosi_as_kof_produces_surface_usable_in_volume_flow(tmp_path: Path) -> None:
    sosi_path = tmp_path / "surface.sos"
    sosi_path.write_text(
        "\n".join(
            [
                ".HODE",
                "..TEGNSETT UTF-8",
                "..TRANSPAR",
                "...ENHET 1",
                "...ORIGO-NØ 0 0",
                ".PUNKT 1:",
                "..OBJTYPE Kontrollpunkt",
                "..NØH",
                "1000 2000 10",
                ".KURVE 2:",
                "..OBJTYPE Terrenglinje",
                "..NØH",
                "1000 2000 10",
                "1001 2000 10",
                "1001 2001 11",
                "1000 2001 11",
                ".SLUTT",
                "",
            ]
        ),
        encoding="utf-8",
    )

    surface = parse_sosi_as_kof(str(sosi_path))

    assert surface.path == str(sosi_path)
    assert len(surface.all_points) == 5
    assert any(block.is_polyline for block in surface.blocks)
    assert surface.ensure_triangulation()

    bounds = surface.xy_bounds()
    assert bounds is not None
    base = ConstantElevationSurface("base", z_value=9.0, bounds=bounds)

    raster_result = compute_volume(surface, base, method="raster", target_cells=30)
    triangle_result = compute_volume(surface, base, method="triangles")

    assert raster_result is not None
    assert triangle_result is not None
    assert raster_result.area == pytest.approx(1.0, rel=0.05)
    assert triangle_result.area == pytest.approx(1.0, rel=0.05)
