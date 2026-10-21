from __future__ import annotations

import os
from typing import Any, Optional, Sequence

from .models import KOFFile, LandXMLSurface


def export_surfaces_to_gpkg(
    files: Sequence[object],
    output_gpkg_path: str,
    *,
    points_layer: str = "surface_points",
    polylines_layer: str = "surface_polylines",
    triangles_layer: str = "surface_triangles",
    crs: Optional[str] = None,
) -> dict[str, Any]:
    """Export loaded vector surfaces to a GeoPackage.

    - KOF/SOSI-derived surfaces: points + original polyline blocks.
    - LandXML surfaces: points + TIN triangle polygons.
    """
    if not files:
        raise ValueError("No surface layers were provided for export.")

    try:
        import geopandas as gpd
        from shapely.geometry import LineString, Point, Polygon
    except ImportError as exc:
        raise ImportError(
            "GeoPackage export requires 'geopandas' and 'shapely'."
        ) from exc

    point_rows: list[dict[str, Any]] = []
    polyline_rows: list[dict[str, Any]] = []
    triangle_rows: list[dict[str, Any]] = []

    for file_idx, surface in enumerate(files):
        source_path = getattr(surface, "path", "")
        source_name = os.path.basename(source_path) if source_path else getattr(surface, "name", f"surface_{file_idx}")
        surface_name = getattr(surface, "name", source_name)

        if isinstance(surface, KOFFile):
            for block_idx, block in enumerate(surface.blocks):
                for point_idx, p in enumerate(block.points):
                    point_rows.append(
                        {
                            "surface_name": surface_name,
                            "surface_type": "kof",
                            "source_file": source_name,
                            "source_path": surface.path,
                            "file_index": file_idx,
                            "block_index": block_idx,
                            "point_index": point_idx,
                            "is_polyline": bool(block.is_polyline),
                            "Pkt": p.pkt,
                            "Code": p.code,
                            "X": float(p.x),
                            "Y": float(p.y),
                            "H": float(p.h),
                            "line_number": int(p.line_number),
                            "geometry": Point(p.y, p.x, p.h),
                        }
                    )

                if block.is_polyline and len(block.points) >= 2:
                    polyline_rows.append(
                        {
                            "surface_name": surface_name,
                            "surface_type": "kof",
                            "source_file": source_name,
                            "source_path": surface.path,
                            "file_index": file_idx,
                            "block_index": block_idx,
                            "point_count": len(block.points),
                            "geometry": LineString(
                                [(p.y, p.x, p.h) for p in block.points]
                            ),
                        }
                    )
            continue

        if isinstance(surface, LandXMLSurface):
            for point_idx, (y_easting, x_northing, z_height) in enumerate(surface.points):
                point_rows.append(
                    {
                        "surface_name": surface_name,
                        "surface_type": "landxml",
                        "source_file": source_name,
                        "source_path": surface.path,
                        "file_index": file_idx,
                        "block_index": None,
                        "point_index": point_idx,
                        "is_polyline": False,
                        "Pkt": f"{surface.layer_name}-{point_idx}",
                        "Code": surface.layer_name,
                        "X": float(x_northing),
                        "Y": float(y_easting),
                        "H": float(z_height),
                        "line_number": point_idx + 1,
                        "geometry": Point(y_easting, x_northing, z_height),
                    }
                )

            for tri_idx, face in enumerate(surface.faces):
                if len(face) != 3:
                    continue
                try:
                    p0 = surface.points[face[0]]
                    p1 = surface.points[face[1]]
                    p2 = surface.points[face[2]]
                except IndexError:
                    continue
                triangle_rows.append(
                    {
                        "surface_name": surface_name,
                        "surface_type": "landxml",
                        "source_file": source_name,
                        "source_path": surface.path,
                        "file_index": file_idx,
                        "triangle_index": tri_idx,
                        "geometry": Polygon(
                            [
                                (p0[0], p0[1], p0[2]),
                                (p1[0], p1[1], p1[2]),
                                (p2[0], p2[1], p2[2]),
                                (p0[0], p0[1], p0[2]),
                            ]
                        ),
                    }
                )
            continue

        raise TypeError(f"Unsupported surface type for GeoPackage export: {type(surface).__name__}")

    if not point_rows:
        raise ValueError("No points found in the selected surface layers.")

    output_dir = os.path.dirname(output_gpkg_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    points_gdf = gpd.GeoDataFrame(point_rows, geometry="geometry", crs=crs)
    points_gdf.to_file(output_gpkg_path, layer=points_layer, driver="GPKG", mode="w")

    polyline_count = 0
    if polyline_rows:
        polylines_gdf = gpd.GeoDataFrame(polyline_rows, geometry="geometry", crs=crs)
        polylines_gdf.to_file(
            output_gpkg_path,
            layer=polylines_layer,
            driver="GPKG",
            mode="a",
        )
        polyline_count = len(polylines_gdf)

    triangle_count = 0
    if triangle_rows:
        triangles_gdf = gpd.GeoDataFrame(triangle_rows, geometry="geometry", crs=crs)
        triangles_gdf.to_file(
            output_gpkg_path,
            layer=triangles_layer,
            driver="GPKG",
            mode="a",
        )
        triangle_count = len(triangles_gdf)

    return {
        "path": output_gpkg_path,
        "points_layer": points_layer,
        "polylines_layer": polylines_layer,
        "triangles_layer": triangles_layer,
        "point_count": len(points_gdf),
        "polyline_count": polyline_count,
        "triangle_count": triangle_count,
        "file_count": len(files),
    }


def export_kof_files_to_gpkg(
    files: Sequence[KOFFile],
    output_gpkg_path: str,
    *,
    points_layer: str = "kof_points",
    polylines_layer: str = "kof_polylines",
    crs: Optional[str] = None,
) -> dict[str, Any]:
    """Backwards-compatible wrapper for KOF-only exports."""
    return export_surfaces_to_gpkg(
        files=files,
        output_gpkg_path=output_gpkg_path,
        points_layer=points_layer,
        polylines_layer=polylines_layer,
        triangles_layer="kof_triangles",
        crs=crs,
    )
