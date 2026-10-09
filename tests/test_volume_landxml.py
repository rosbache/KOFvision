from __future__ import annotations

from pathlib import Path

import pytest

from kofvision.models import LandXMLSurface
from kofvision.landxml_parser import parse_landxml
from kofvision.volume import compute_volume


def _surface(name: str, z_value: float) -> LandXMLSurface:
    points = [
        (0.0, 0.0, z_value),
        (10.0, 0.0, z_value),
        (10.0, 10.0, z_value),
        (0.0, 10.0, z_value),
    ]
    faces = [(0, 1, 2), (0, 2, 3)]
    return LandXMLSurface(path="synthetic.xml", layer_name=name, points=points, faces=faces)


def test_triangles_method_supports_landxml_surface() -> None:
    top = _surface("top", 12.0)
    bottom = _surface("bottom", 10.0)

    result = compute_volume(top, bottom, method="triangles")

    assert result is not None
    assert result.top_name.endswith("::top")
    assert result.bottom_name.endswith("::bottom")
    assert result.area == pytest.approx(100.0)
    assert result.net == pytest.approx(200.0)
    assert result.cut == pytest.approx(200.0)
    assert result.fill == pytest.approx(0.0)


def test_attached_tin_landxml_supports_triangle_volume() -> None:
    path = Path("test data") / "Eksport_veger_trau240624.xml"
    doc = parse_landxml(str(path), station_interval=20.0)

    tin_surface = next((s for s in doc.surfaces if len(s.faces) > 0), None)
    assert tin_surface is not None, "Expected at least one TIN surface with faces in attached file"

    shifted_points = [(y, x, z - 1.0) for (y, x, z) in tin_surface.points]
    shifted_bottom = LandXMLSurface(
        path=tin_surface.path,
        layer_name=f"{tin_surface.layer_name}_minus1m",
        points=shifted_points,
        faces=list(tin_surface.faces),
    )

    result = compute_volume(tin_surface, shifted_bottom, method="triangles")
    assert result is not None
    assert result.area > 0.0
    assert result.net > 0.0
    assert result.cut > 0.0
    assert result.fill == pytest.approx(0.0, abs=1e-6)
