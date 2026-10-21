from __future__ import annotations

import sys
import types
from pathlib import Path

from kofvision.export import export_surfaces_to_gpkg
from kofvision.models import KOFBlock, KOFFile, KOFPoint, LandXMLSurface


class _FakeGeoDataFrame:
    writes: list[dict[str, object]] = []

    def __init__(self, rows: list[dict[str, object]], *, geometry: str, crs: str | None) -> None:
        self._rows = list(rows)
        self.geometry = geometry
        self.crs = crs

    def to_file(self, path: str, *, layer: str, driver: str, mode: str = "w") -> None:
        self.writes.append(
            {
                "path": path,
                "layer": layer,
                "driver": driver,
                "mode": mode,
                "count": len(self._rows),
            }
        )

    def __len__(self) -> int:
        return len(self._rows)


class _FakePoint:
    def __init__(self, *coords: float) -> None:
        self.coords = coords


class _FakeLineString:
    def __init__(self, coords: list[tuple[float, float, float]]) -> None:
        self.coords = coords


class _FakePolygon:
    def __init__(self, coords: list[tuple[float, float, float]]) -> None:
        self.coords = coords


def test_export_surfaces_to_gpkg_supports_kof_and_landxml(tmp_path: Path, monkeypatch) -> None:
    fake_gpd = types.ModuleType("geopandas")
    _FakeGeoDataFrame.writes = []
    fake_gpd.GeoDataFrame = _FakeGeoDataFrame

    fake_geom = types.ModuleType("shapely.geometry")
    fake_geom.Point = _FakePoint
    fake_geom.LineString = _FakeLineString
    fake_geom.Polygon = _FakePolygon
    fake_shapely = types.ModuleType("shapely")
    fake_shapely.geometry = fake_geom

    monkeypatch.setitem(sys.modules, "geopandas", fake_gpd)
    monkeypatch.setitem(sys.modules, "shapely", fake_shapely)
    monkeypatch.setitem(sys.modules, "shapely.geometry", fake_geom)

    kof = KOFFile(
        path=str(tmp_path / "input.kof"),
        blocks=[
            KOFBlock(
                points=[
                    KOFPoint(pkt="1", code="A", x=1000.0, y=2000.0, h=10.0, line_number=1),
                    KOFPoint(pkt="2", code="A", x=1001.0, y=2001.0, h=10.5, line_number=2),
                ],
                is_polyline=True,
            )
        ],
    )
    landxml = LandXMLSurface(
        path=str(tmp_path / "input.xml"),
        layer_name="TIN",
        points=[(3000.0, 4000.0, 20.0), (3001.0, 4000.0, 21.0), (3001.0, 4001.0, 22.0)],
        faces=[(0, 1, 2)],
    )

    out_path = tmp_path / "surfaces.gpkg"
    summary = export_surfaces_to_gpkg([kof, landxml], str(out_path))

    assert summary["path"] == str(out_path)
    assert summary["point_count"] == 5
    assert summary["polyline_count"] == 1
    assert summary["triangle_count"] == 1
    assert summary["file_count"] == 2

    layers = {entry["layer"]: entry for entry in _FakeGeoDataFrame.writes}
    assert layers["surface_points"]["count"] == 5
    assert layers["surface_points"]["mode"] == "w"
    assert layers["surface_polylines"]["count"] == 1
    assert layers["surface_polylines"]["mode"] == "a"
    assert layers["surface_triangles"]["count"] == 1
    assert layers["surface_triangles"]["mode"] == "a"
