from __future__ import annotations

import os
from typing import Any, Optional, Sequence

from .models import KOFFile


def export_kof_files_to_gpkg(
    files: Sequence[KOFFile],
    output_gpkg_path: str,
    *,
    points_layer: str = "kof_points",
    polylines_layer: str = "kof_polylines",
    crs: Optional[str] = None,
) -> dict[str, Any]:
    """Export original KOF data (points + polyline blocks) to a GeoPackage.

    The export includes only source geometries from the parsed KOF files.
    Triangulation data is intentionally excluded.
    """
    if not files:
        raise ValueError("No KOF files were provided for export.")

    try:
        import geopandas as gpd
        from shapely.geometry import LineString, Point
    except ImportError as exc:
        raise ImportError(
            "GeoPackage export requires 'geopandas' and 'shapely'."
        ) from exc

    point_rows: list[dict[str, Any]] = []
    polyline_rows: list[dict[str, Any]] = []

    for file_idx, kof in enumerate(files):
        source_name = os.path.basename(kof.path)
        for block_idx, block in enumerate(kof.blocks):
            for point_idx, p in enumerate(block.points):
                point_rows.append(
                    {
                        "source_file": source_name,
                        "source_path": kof.path,
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
                        "source_file": source_name,
                        "source_path": kof.path,
                        "file_index": file_idx,
                        "block_index": block_idx,
                        "point_count": len(block.points),
                        "geometry": LineString(
                            [(p.y, p.x, p.h) for p in block.points]
                        ),
                    }
                )

    if not point_rows:
        raise ValueError("No points found in the loaded KOF files.")

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

    return {
        "path": output_gpkg_path,
        "points_layer": points_layer,
        "polylines_layer": polylines_layer,
        "point_count": len(points_gdf),
        "polyline_count": polyline_count,
        "file_count": len(files),
    }
